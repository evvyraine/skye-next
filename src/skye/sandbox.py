from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import shutil
import time
import uuid
import zipfile
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

import structlog
from agents import FunctionTool, function_tool
from openai import AsyncOpenAI

from .artifacts import GeneratedFile
from .models import Scope

log = structlog.get_logger()
MAX_COMMAND_CHARS = 4_000
MAX_PYTHON_CHARS = 20_000
MAX_WRITE_BYTES = 5 * 1024 * 1024
MAX_READ_BYTES = 100_000
MAX_OUTPUT_BYTES = 64 * 1024
MAX_DELIVER_FILES = 10
MAX_CHANGED_FILES = 50
DEFAULT_TTL_SECONDS = 7 * 86_400
DEFAULT_SCOPE_BYTES = 1_073_741_824
DEFAULT_TOTAL_BYTES = 21_474_836_480
DEFAULT_MAX_CONCURRENT = 4
SCOPES_DIRNAME = "scopes"
LAST_USED_NAME = ".skye-last-used"
SKIP_PARTS = frozenset(
    {"__pycache__", ".git", ".venv", "venv", "node_modules", "site-packages"}
)
_IMAGE_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")


@dataclass(frozen=True, slots=True)
class SandboxResult:
    stdout: str
    stderr: str
    timed_out: bool


class SandboxUnavailableError(RuntimeError):
    pass


class SandboxService:
    """Persistent workspace per scope, one fresh container per command.

    Each user (private chat) or chat (group) gets its own directory on the
    shared volume. Commands run in a throwaway, hardened container that mounts
    only that directory at ``/work``; files survive between commands, turns, and
    restarts. There is no long-lived container and no host execution.
    """

    def __init__(
        self,
        image: str,
        timeout_seconds: int,
        max_bytes: int,
        *,
        allow_network: bool = False,
        volume: str = "skye-sandbox-work",
        work_dir: Path = Path("/sandbox-work"),
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        scope_bytes: int = DEFAULT_SCOPE_BYTES,
        total_bytes: int = DEFAULT_TOTAL_BYTES,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self.image = image
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.allow_network = allow_network
        self.volume = volume
        self.work_dir = work_dir
        self.ttl_seconds = ttl_seconds
        self.scope_bytes = scope_bytes
        self.total_bytes = total_bytes
        self.max_concurrent = max_concurrent
        self._slots = asyncio.BoundedSemaphore(max_concurrent)
        self._mountpoint: str | None = None
        self._mountpoint_checked = False

    def new_workspace(self, scope: Scope) -> ScopeSandbox:
        return ScopeSandbox(self, scope, self.workspace_path(scope))

    def workspace_path(self, scope: Scope) -> Path:
        path = self.work_dir / SCOPES_DIRNAME / _scope_key(scope)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def snapshot(self, scope: Scope) -> dict[str, tuple[int, int]]:
        """Relative path -> (mtime_ns, size) for deliverable files."""
        workspace = self.workspace_path(scope)
        stamps: dict[str, tuple[int, int]] = {}
        for path in workspace.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(workspace).as_posix()
            if _skip_relative(relative):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            stamps[relative] = (stat.st_mtime_ns, stat.st_size)
        return stamps

    def changes(
        self, scope: Scope, before: dict[str, tuple[int, int]]
    ) -> list[tuple[str, bytes]]:
        workspace = self.workspace_path(scope)
        changed = sorted(
            relative
            for relative, stamp in self.snapshot(scope).items()
            if before.get(relative) != stamp
        )
        found: list[tuple[str, bytes]] = []
        for relative in changed[:MAX_CHANGED_FILES]:
            path = workspace / relative
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if not data or len(data) > self.max_bytes:
                continue
            found.append((relative, data))
        return found

    def read_text(self, scope: Scope, path: str, max_bytes: int = MAX_READ_BYTES) -> str:
        target = self.resolve(scope, path)
        if not target.is_file():
            raise FileNotFoundError(path)
        data = target.read_bytes()
        if len(data) > max_bytes:
            return (
                f"{path} is {len(data)} bytes, larger than the {max_bytes} byte read limit. "
                "Use shell_exec to inspect it piece by piece."
            )
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return f"{path} is binary ({len(data)} bytes). Use shell_exec to process it."

    def write_bytes(self, scope: Scope, path: str, data: bytes) -> int:
        if len(data) > MAX_WRITE_BYTES:
            raise ValueError(
                f"That file is too large (maximum {MAX_WRITE_BYTES // 1024 // 1024} MB)."
            )
        target = self.resolve(scope, path)
        if self._scope_size(self.workspace_path(scope)) + len(data) > self.scope_bytes:
            raise ValueError("The workspace is full. Delete some files first.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self._touch(scope)
        return len(data)

    def resolve(self, scope: Scope, path: str) -> Path:
        workspace = self.workspace_path(scope).resolve()
        cleaned = path.replace("\\", "/").strip().lstrip("/")
        if not cleaned or ".." in PurePosixPath(cleaned).parts:
            raise ValueError("Use a relative path inside the workspace.")
        candidate = (workspace / cleaned).resolve()
        if candidate != workspace and workspace not in candidate.parents:
            raise ValueError("Use a relative path inside the workspace.")
        return candidate

    async def execute(
        self,
        scope: Scope,
        command: str,
        *,
        stdin: bytes | None = None,
        timeout: float | None = None,
    ) -> SandboxResult:
        if not shutil.which("docker"):
            raise SandboxUnavailableError("Docker is not available.")
        self._touch(scope)
        mount, workdir = await self._container_target(scope)
        seconds = timeout if timeout is not None else float(self.timeout_seconds)
        name = f"skye-sbx-{uuid.uuid4().hex[:16]}"
        argv = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--name",
            name,
            "--network",
            "default" if self.allow_network else "none",
            "--memory",
            "512m",
            "--cpus",
            "1.0",
            "--pids-limit",
            "128",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=64m,mode=1777",
            "--user",
            self._run_user(),
            "-e",
            f"HOME={workdir}",
            "-e",
            "PIP_USER=1",
            "-e",
            f"PIP_CACHE_DIR={workdir}/.cache/pip",
            "-e",
            f"PATH={workdir}/.local/bin:/usr/local/bin:/usr/bin:/bin",
            *mount,
            "-w",
            workdir,
            self.image,
            "sh",
            "-c",
            command,
        ]
        async with self._slots:
            try:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.PIPE
                    if stdin is not None
                    else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except (FileNotFoundError, OSError) as error:
                log.warning("sandbox_spawn_failed", error=type(error).__name__)
                raise SandboxUnavailableError("The sandbox could not start.") from error
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(input=stdin), timeout=seconds
                )
            except TimeoutError:
                with suppress(Exception):
                    process.kill()
                await process.wait()
                await self._force_remove(name)
                return SandboxResult("", "", True)
        return SandboxResult(_truncate(stdout), _truncate(stderr), False)

    async def run_janitor(self, interval: float = 3_600.0) -> None:
        while True:
            try:
                await asyncio.to_thread(self.cleanup)
            except Exception as error:  # noqa: BLE001 - the loop must survive
                log.warning("sandbox_janitor_failed", error=type(error).__name__)
            await asyncio.sleep(interval)

    def cleanup(self) -> None:
        root = self.work_dir / SCOPES_DIRNAME
        if not root.is_dir():
            return
        now = time.time()
        entries: list[tuple[float, int, Path]] = []
        for scope_dir in root.iterdir():
            if not scope_dir.is_dir():
                continue
            try:
                last_used = (scope_dir / LAST_USED_NAME).stat().st_mtime
            except OSError:
                try:
                    last_used = scope_dir.stat().st_mtime
                except OSError:
                    continue
            entries.append((last_used, _dir_size(scope_dir), scope_dir))
        expired = (
            [item for item in entries if now - item[0] > self.ttl_seconds]
            if self.ttl_seconds > 0
            else []
        )
        for _, _, path in expired:
            shutil.rmtree(path, ignore_errors=True)
            log.info("sandbox_scope_expired", scope=path.name)
        kept = [item for item in entries if item not in expired]
        total = sum(size for _, size, _ in kept)
        if total > self.total_bytes:
            for _, size, path in sorted(kept):
                if total <= self.total_bytes:
                    break
                shutil.rmtree(path, ignore_errors=True)
                total -= size
                log.info("sandbox_scope_evicted", scope=path.name)

    def _scope_size(self, path: Path) -> int:
        return _dir_size(path)

    def _touch(self, scope: Scope) -> None:
        try:
            marker = self.workspace_path(scope) / LAST_USED_NAME
            marker.write_bytes(str(int(time.time())).encode())
        except OSError:
            log.warning("sandbox_touch_failed", scope=_scope_key(scope))

    async def _container_target(self, scope: Scope) -> tuple[list[str], str]:
        host = await self._volume_mountpoint()
        if host is not None:
            source = str(Path(host) / SCOPES_DIRNAME / _scope_key(scope))
            return (["--mount", f"type=bind,src={source},dst=/work"], "/work")
        log.warning("sandbox_volume_unresolved", volume=self.volume)
        return (["-v", f"{self.volume}:/work"], f"/work/{SCOPES_DIRNAME}/{_scope_key(scope)}")

    async def _volume_mountpoint(self) -> str | None:
        if self._mountpoint_checked:
            return self._mountpoint
        self._mountpoint_checked = True
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "volume",
                "inspect",
                self.volume,
                "--format",
                "{{.Mountpoint}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
            value = stdout.decode("utf-8", "replace").strip()
            if process.returncode == 0 and value:
                self._mountpoint = value
        except (OSError, TimeoutError):
            self._mountpoint = None
        return self._mountpoint

    async def _force_remove(self, name: str) -> None:
        with suppress(Exception):
            process = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "-f",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), timeout=10)

    @staticmethod
    def _run_user() -> str:
        uid, gid = os.getuid(), os.getgid()
        if uid == 0:
            uid, gid = 10001, 10001
        return f"{uid}:{gid}"


@dataclass(slots=True)
class ScopeSandbox:
    """Per-turn view over a scope workspace: seeds inputs and exposes tools."""

    service: SandboxService
    scope: Scope
    workspace: Path
    _before: dict[str, tuple[int, int]] = field(default_factory=dict)

    def seed(self, files: list[tuple[str, bytes]]) -> None:
        for name, data in files:
            safe = _safe_name(name)
            if safe is None:
                continue
            unique = self._unique_name(safe)
            try:
                self.service.write_bytes(self.scope, unique, data)
            except ValueError as error:
                log.warning("sandbox_seed_rejected", name=unique, error=str(error))

    def _unique_name(self, name: str) -> str:
        target = PurePosixPath(name)
        stem, suffix = target.stem, target.suffix
        candidate = name
        counter = 1
        while (self.workspace / candidate).exists():
            counter += 1
            candidate = f"{stem}-{counter}{suffix}"
        return candidate

    def mark(self) -> None:
        self.service._touch(self.scope)
        self._before = self.service.snapshot(self.scope)

    def mark_used(self) -> None:
        self.service._touch(self.scope)

    def deliverable(
        self, exclude: Iterable[GeneratedFile] = ()
    ) -> tuple[tuple[GeneratedFile, ...], tuple[bytes, ...]]:
        delivered = {(item.filename, _digest(item.data)) for item in exclude}
        images: list[bytes] = []
        documents: list[tuple[str, bytes]] = []
        for relative, data in self.service.changes(self.scope, self._before):
            name = PurePosixPath(relative).name
            if (name, _digest(data)) in delivered:
                continue
            if _looks_like_image(data):
                images.append(data)
            else:
                documents.append((relative, data))
        return _package(documents), tuple(images[:MAX_DELIVER_FILES])

    def tools(self) -> list[FunctionTool]:
        box = self

        @function_tool
        async def shell_exec(command: str) -> str:
            """Run a shell command in the persistent Linux workspace.

            The workspace is mounted at /work and keeps files between commands
            and messages. Prefer non-interactive commands and short outputs.

            Args:
                command: Shell command to run, up to 4000 characters.
            """
            if not command.strip():
                return "Pass the command to run."
            if len(command) > MAX_COMMAND_CHARS:
                return "That command is too long. Split it into smaller steps."
            return await box._run(command.strip())

        @function_tool
        async def python(code: str) -> str:
            """Run Python code in the persistent Linux workspace.

            The code runs at /work and can read and write workspace files, so
            imports and files it creates survive between messages. Prefer this
            over shell_exec for Python work.

            Args:
                code: Python source to run, up to 20000 characters.
            """
            if not code.strip():
                return "Pass the Python code to run."
            if len(code) > MAX_PYTHON_CHARS:
                return "That code is too long. Split it into smaller steps."
            return await box._run("python -", stdin=code.encode("utf-8"))

        @function_tool
        async def read_file(path: str) -> str:
            """Read a text file from the workspace.

            Args:
                path: Workspace-relative path, for example notes/report.md.
            """
            try:
                return box.service.read_text(box.scope, path)
            except ValueError as error:
                return str(error)
            except FileNotFoundError:
                return f"No file at {path} in the workspace."
            except OSError as error:
                return f"Could not read {path}: {type(error).__name__}."

        @function_tool
        async def write_file(path: str, content: str) -> str:
            """Write a UTF-8 text file into the workspace.

            Args:
                path: Workspace-relative path, for example notes/report.md.
                content: Complete file text.
            """
            try:
                written = box.service.write_bytes(box.scope, path, content.encode("utf-8"))
            except ValueError as error:
                return str(error)
            except OSError as error:
                return f"Could not write {path}: {type(error).__name__}."
            return f"Wrote {written} bytes to {path}."

        return [shell_exec, python, read_file, write_file]

    async def _run(self, command: str, *, stdin: bytes | None = None) -> str:
        try:
            result = await self.service.execute(self.scope, command, stdin=stdin)
        except SandboxUnavailableError as error:
            return str(error)
        parts: list[str] = []
        if result.timed_out:
            parts.append(f"Timed out after {self.service.timeout_seconds}s.")
        if result.stdout:
            parts.append(f"stdout:\n{result.stdout}")
        if result.stderr:
            parts.append(f"stderr:\n{result.stderr}")
        if not parts:
            return "Done, no output."
        return "\n".join(parts).strip()


async def turn_files(
    user_input: str | list[Any],
    client: AsyncOpenAI | None,
    max_bytes: int,
) -> list[tuple[str, bytes]]:
    """Attached turn files as (name, bytes): inline data URLs directly,
    provider file ids downloaded when a client is available."""
    found: list[tuple[str, bytes]] = []
    names: set[str] = set()

    def take(name: str, data: bytes) -> None:
        if not data or len(data) > max_bytes or len(found) >= MAX_DELIVER_FILES:
            return
        candidate = name or f"attached-{len(found)}"
        base = candidate
        counter = 1
        while candidate in names:
            counter += 1
            candidate = f"{base}-{counter}"
        names.add(candidate)
        found.append((candidate, data))

    async def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                await visit(item)
            return
        if not isinstance(value, dict):
            return
        kind = value.get("type")
        if kind == "input_image":
            url = value.get("image_url")
            if isinstance(url, str) and url.startswith("data:") and ";base64," in url:
                header, _, encoded = url.partition(",")
                mime = header.split(";", 1)[0].split(":", 1)[-1]
                try:
                    take(f"attached-{len(found)}.{_extension(mime)}", base64.b64decode(encoded))
                except ValueError:
                    log.warning("sandbox_image_decode_failed")
        elif kind == "input_file":
            data = value.get("file_data")
            if isinstance(data, str) and ";base64," in data:
                _, _, encoded = data.partition(",")
                try:
                    take(str(value.get("filename", "")), base64.b64decode(encoded))
                except ValueError:
                    log.warning("sandbox_file_decode_failed")
            elif client is not None and isinstance(value.get("file_id"), str):
                downloaded = await _download_file(client, str(value["file_id"]))
                if downloaded is not None:
                    take(str(value.get("filename", "")), downloaded)
        for item in value.values():
            if len(found) >= MAX_DELIVER_FILES:
                return
            await visit(item)

    await visit(user_input)
    return found


async def _download_file(client: AsyncOpenAI, file_id: str) -> bytes | None:
    try:
        content = await client.files.content(file_id)
    except Exception as error:  # noqa: BLE001 - provider errors are not fatal
        log.warning("sandbox_file_download_failed", error=type(error).__name__)
        return None
    data = getattr(content, "content", None)
    if isinstance(data, bytes):
        return data
    read = getattr(content, "read", None)
    if callable(read):
        result = read()
        if isinstance(result, bytes):
            return result
    return None


def _package(documents: list[tuple[str, bytes]]) -> tuple[GeneratedFile, ...]:
    if not documents:
        return ()
    if len(documents) == 1:
        relative, data = documents[0]
        return (GeneratedFile(PurePosixPath(relative).name[:200], data),)
    return (_zip_files(documents),)


def _zip_files(documents: list[tuple[str, bytes]]) -> GeneratedFile:
    directory = PurePosixPath(documents[0][0]).parts[0]
    name = directory if all(doc[0].startswith(f"{directory}/") for doc in documents) else "files"
    if "." in name:
        name = "files"
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, data in documents:
            archive.writestr(relative, data)
    return GeneratedFile(f"{name or 'files'}.zip", buffer.getvalue())


def _scope_key(scope: Scope) -> str:
    return f"{scope.kind[0]}{scope.id}"


def _skip_relative(relative: str) -> bool:
    return any(
        part.startswith(".") or part in SKIP_PARTS for part in PurePosixPath(relative).parts
    )


def _looks_like_image(data: bytes) -> bool:
    if data.startswith(_IMAGE_SIGNATURES):
        return True
    return data.startswith(b"RIFF") and data[8:12] == b"WEBP"


def _digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _safe_name(name: str) -> str | None:
    cleaned = name.replace("\\", "/").strip().lstrip("/")
    if not cleaned or ".." in PurePosixPath(cleaned).parts:
        return None
    return cleaned[:200]


def _extension(mime: str) -> str:
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
    }.get(mime, "bin")


def _truncate(data: bytes) -> str:
    text = data[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace").strip()
    if len(data) > MAX_OUTPUT_BYTES:
        text += "\n[output truncated]"
    return text


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            with suppress(OSError):
                total += (Path(root) / name).stat().st_size
    return total
