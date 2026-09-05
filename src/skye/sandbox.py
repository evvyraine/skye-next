from __future__ import annotations

import asyncio
import base64
import shutil
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import structlog
from agents import FunctionTool, function_tool
from openai import AsyncOpenAI

log = structlog.get_logger()
MAX_COMMAND_CHARS = 4_000
MAX_OUTPUT_BYTES = 64 * 1024
MAX_OUTPUT_FILES = 10
SKIP_DIRS = frozenset({"__pycache__", ".git", ".venv", "node_modules"})


@dataclass(frozen=True, slots=True)
class SandboxResult:
    stdout: str
    stderr: str
    timed_out: bool
    files: tuple[tuple[str, bytes], ...]


class SandboxService:
    """Own code sandbox: one Docker container per command, no host execution.

    Each turn gets a fresh work directory that persists across its shell_exec
    calls and is wiped afterwards. Containers run with capped memory/CPU/pids
    and no network unless explicitly allowed.
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
    ) -> None:
        self.image = image
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.allow_network = allow_network
        self.volume = volume
        self.work_dir = work_dir

    def new_turn(self) -> TurnSandbox:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        path = Path(tempfile.mkdtemp(prefix="turn-", dir=str(self.work_dir)))
        return TurnSandbox(self, path)

    async def execute(self, workdir: Path, command: str) -> SandboxResult:
        if not shutil.which("docker"):
            raise SandboxUnavailableError("Docker is not available.")
        try:
            container_workdir = f"/work/{workdir.relative_to(self.work_dir).as_posix()}"
        except ValueError as error:
            raise SandboxUnavailableError("The sandbox work directory is invalid.") from error
        argv = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "default" if self.allow_network else "none",
            "--memory",
            "512m",
            "--cpus",
            "1.0",
            "--pids-limit",
            "128",
            "-v",
            f"{self.volume}:/work",
            "-w",
            container_workdir,
            self.image,
            "sh",
            "-c",
            command,
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self.timeout_seconds
                )
            except TimeoutError:
                with suppress(Exception):
                    process.kill()
                await process.wait()
                return SandboxResult("", "", True, ())
        except FileNotFoundError as error:
            raise SandboxUnavailableError("Docker is not available.") from error
        except OSError as error:
            log.warning("sandbox_spawn_failed", error=type(error).__name__)
            raise SandboxUnavailableError("The sandbox could not start.") from error
        return SandboxResult(
            _truncate(stdout),
            _truncate(stderr),
            False,
            collect_outputs(workdir, self.max_bytes),
        )


class SandboxUnavailableError(RuntimeError):
    pass


@dataclass(slots=True)
class TurnSandbox:
    """Per-turn sandbox state: workdir, inputs, and the shell_exec tool."""

    service: SandboxService
    workdir: Path
    _inputs: set[str] = field(default_factory=set)

    def seed(self, files: list[tuple[str, bytes]]) -> None:
        for name, data in files:
            safe = _safe_name(name)
            if safe is None:
                continue
            target = self.workdir / safe
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            self._inputs.add(safe)

    def close(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def tools(self) -> list[FunctionTool]:
        turn = self

        @function_tool
        async def shell_exec(command: str) -> str:
            """Run a shell command in the turn sandbox.

            The work directory persists across calls within this turn, so write
            files once and reuse them. Prefer non-interactive commands and short
            outputs. To deliver a file, read it back and pass it to deliver_file.

            Args:
                command: Shell command to run, up to 4000 characters.
            """
            if not command.strip():
                return "Pass the command to run."
            if len(command) > MAX_COMMAND_CHARS:
                return "That command is too long. Split it into smaller steps."
            try:
                result = await turn.service.execute(turn.workdir, command.strip())
            except SandboxUnavailableError as error:
                return str(error)
            parts = []
            if result.timed_out:
                parts.append(f"Timed out after {turn.service.timeout_seconds}s.")
            if result.stdout:
                parts.append(f"stdout:\n{result.stdout}")
            if result.stderr:
                parts.append(f"stderr:\n{result.stderr}")
            if result.files:
                listed = ", ".join(name for name, _ in result.files)
                parts.append(f"New files: {listed}. Read one back to deliver it.")
            if not parts:
                return "Done, no output."
            return "\n".join(parts).strip()

        return [shell_exec]


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
        if not data or len(data) > max_bytes or len(found) >= MAX_OUTPUT_FILES:
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
            if len(found) >= MAX_OUTPUT_FILES:
                return
            await visit(item)

    await visit(user_input)
    return found


async def _download_file(client: AsyncOpenAI, file_id: str) -> bytes | None:
    try:
        content = await client.files.content(file_id)
    except Exception as error:
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


def collect_outputs(workdir: Path, max_bytes: int) -> tuple[tuple[str, bytes], ...]:
    outputs: list[tuple[str, bytes]] = []
    for path in sorted(workdir.rglob("*")):
        if len(outputs) >= MAX_OUTPUT_FILES or not path.is_file():
            continue
        relative = path.relative_to(workdir).as_posix()
        if relative.split("/")[0] in SKIP_DIRS or path.name.startswith("."):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if not data or len(data) > max_bytes:
            continue
        outputs.append((relative, data))
    return tuple(outputs)


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
