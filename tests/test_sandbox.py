import base64
import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from agents import FunctionTool

from skye.artifacts import GeneratedFile
from skye.models import Scope
from skye.sandbox import (
    LAST_USED_NAME,
    SandboxResult,
    SandboxService,
    SandboxUnavailableError,
    turn_files,
)


def tool_context(name: str, payload: str) -> Any:
    from agents.tool_context import ToolContext

    return ToolContext(
        context=None,
        tool_name=name,
        tool_arguments=payload,
        tool_call_id="call_1",
        run_config=None,
    )


def service(tmp_path: Path, **overrides: Any) -> SandboxService:
    options: dict[str, Any] = {
        "volume": "test-sandbox-work",
        "work_dir": tmp_path / "work",
    }
    options.update(overrides)
    return SandboxService("python:3.14-slim", 10, 1024, **options)


def tools(box: Any) -> dict[str, FunctionTool]:
    return {tool.name: tool for tool in box.tools()}


async def invoke(tool: FunctionTool, **arguments: Any) -> str:
    payload = json.dumps(arguments)
    return await tool.on_invoke_tool(tool_context(tool.name, payload), payload)


def test_seed_writes_safe_paths_only(tmp_path: Path) -> None:
    box = service(tmp_path).new_workspace(Scope("user", 7))

    box.seed([("report.txt", b"hi"), ("../evil.txt", b"no"), ("", b"empty")])

    assert (box.workspace / "report.txt").read_bytes() == b"hi"
    assert not (box.workspace / "evil.txt").exists()
    assert (box.workspace / LAST_USED_NAME).exists()


def test_seed_does_not_overwrite_existing_files(tmp_path: Path) -> None:
    box = service(tmp_path).new_workspace(Scope("user", 7))
    (box.workspace / "report.txt").write_bytes(b"user work")

    box.seed([("report.txt", b"attachment")])

    assert (box.workspace / "report.txt").read_bytes() == b"user work"
    assert (box.workspace / "report-2.txt").read_bytes() == b"attachment"


def test_changes_detect_new_and_modified_files(tmp_path: Path) -> None:
    sandbox = service(tmp_path)
    scope = Scope("user", 7)
    box = sandbox.new_workspace(scope)
    (box.workspace / "keep.txt").write_bytes(b"same")
    (box.workspace / "edit.txt").write_bytes(b"before")
    before = sandbox.snapshot(scope)

    time.sleep(0.01)
    (box.workspace / "edit.txt").write_bytes(b"after!")
    (box.workspace / "new.txt").write_bytes(b"new")

    assert dict(sandbox.changes(scope, before)) == {
        "edit.txt": b"after!",
        "new.txt": b"new",
    }


def test_changes_skip_hidden_and_ignored_paths(tmp_path: Path) -> None:
    sandbox = service(tmp_path)
    scope = Scope("user", 7)
    box = sandbox.new_workspace(scope)
    before = sandbox.snapshot(scope)

    (box.workspace / ".hidden").write_bytes(b"x")
    (box.workspace / "__pycache__").mkdir()
    (box.workspace / "__pycache__" / "m.pyc").write_bytes(b"x")
    (box.workspace / "ok.txt").write_bytes(b"ok")

    assert sandbox.changes(scope, before) == [("ok.txt", b"ok")]


def test_deliverable_separates_images_and_documents(tmp_path: Path) -> None:
    sandbox = service(tmp_path)
    box = sandbox.new_workspace(Scope("user", 7))
    box.mark()

    (box.workspace / "report.txt").write_bytes(b"report")
    (box.workspace / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\nhead")

    files, images = box.deliverable()

    assert [item.filename for item in files] == ["report.txt"]
    assert files[0].data == b"report"
    assert images == (b"\x89PNG\r\n\x1a\nhead",)


def test_deliverable_skips_explicitly_delivered(tmp_path: Path) -> None:
    sandbox = service(tmp_path)
    box = sandbox.new_workspace(Scope("user", 7))
    box.mark()
    (box.workspace / "report.txt").write_bytes(b"report")

    files, images = box.deliverable([GeneratedFile("report.txt", b"report")])

    assert files == ()
    assert images == ()


def test_cleanup_removes_expired_scopes(tmp_path: Path) -> None:
    sandbox = service(tmp_path, ttl_seconds=10)
    old = sandbox.workspace_path(Scope("user", 1))
    (old / "file.txt").write_bytes(b"x")
    marker = old / LAST_USED_NAME
    marker.write_bytes(b"0")
    stale = time.time() - 3600
    os.utime(marker, (stale, stale))
    fresh = sandbox.workspace_path(Scope("user", 2))
    (fresh / "file.txt").write_bytes(b"x")

    sandbox.cleanup()

    assert not old.exists()
    assert fresh.exists()


async def test_turn_files_reads_inline_data() -> None:
    encoded = base64.b64encode(b"png").decode()
    user_input: list[Any] = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "chart this"},
                {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"},
                {
                    "type": "input_file",
                    "filename": "notes.txt",
                    "file_data": f"data:text/plain;base64,{base64.b64encode(b'hi').decode()}",
                },
            ],
        }
    ]

    assert await turn_files(user_input, None, 1024) == [
        ("attached-0.png", b"png"),
        ("notes.txt", b"hi"),
    ]


async def test_execute_without_docker_is_unavailable(tmp_path: Path) -> None:
    with (
        patch("skye.sandbox.shutil.which", return_value=None),
        pytest.raises(SandboxUnavailableError),
    ):
        await service(tmp_path).execute(Scope("user", 7), "echo hi")


async def test_tools_validate_their_inputs(tmp_path: Path) -> None:
    box = service(tmp_path).new_workspace(Scope("user", 7))
    available = tools(box)

    assert set(available) == {"shell_exec", "python", "read_file", "write_file"}
    assert await invoke(available["shell_exec"], command="") == "Pass the command to run."
    assert (
        await invoke(available["shell_exec"], command="x" * 4001)
        == "That command is too long. Split it into smaller steps."
    )
    assert await invoke(available["python"], code="") == "Pass the Python code to run."
    assert (
        await invoke(available["write_file"], path="../evil", content="x")
        == "Use a relative path inside the workspace."
    )
    with patch("skye.sandbox.shutil.which", return_value=None):
        assert (
            await invoke(available["shell_exec"], command="echo hi")
            == "Docker is not available."
        )


async def test_read_and_write_round_trip(tmp_path: Path) -> None:
    box = service(tmp_path).new_workspace(Scope("user", 7))
    available = tools(box)

    assert (
        await invoke(available["write_file"], path="notes/a.txt", content="hello")
        == "Wrote 5 bytes to notes/a.txt."
    )
    assert await invoke(available["read_file"], path="notes/a.txt") == "hello"
    assert (
        await invoke(available["read_file"], path="missing.txt")
        == "No file at missing.txt in the workspace."
    )
    assert (
        await invoke(available["read_file"], path="../secret")
        == "Use a relative path inside the workspace."
    )


async def test_python_tool_pipes_code_on_stdin(tmp_path: Path) -> None:
    box = service(tmp_path).new_workspace(Scope("user", 7))
    box.service.execute = AsyncMock(  # type: ignore[method-assign]
        return_value=SandboxResult("hi", "", False)
    )

    output = await invoke(tools(box)["python"], code="print('hi')")

    assert output == "stdout:\nhi"
    box.service.execute.assert_awaited_once()
    assert box.service.execute.await_args.args[1] == "python -"
    assert box.service.execute.await_args.kwargs["stdin"] == b"print('hi')"


async def test_execute_mounts_the_scope_and_hardens_container(tmp_path: Path) -> None:
    spawned: list[list[str]] = []

    async def fake_exec(*argv: str, **_kwargs: object) -> object:
        spawned.append(list(argv))

        class Process:
            returncode = 0

            async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
                return b"out", b""

            def kill(self) -> None:
                return None

            async def wait(self) -> int:
                return 0

        return Process()

    sandbox = service(tmp_path)
    scope = Scope("chat", -100)
    with (
        patch("skye.sandbox.shutil.which", return_value="/usr/bin/docker"),
        patch.object(
            SandboxService,
            "_volume_mountpoint",
            new=AsyncMock(return_value="/host/volume"),
        ),
        patch("skye.sandbox.asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        result = await sandbox.execute(scope, "echo hi")

    assert result.stdout == "out"
    argv = spawned[0]
    assert argv[argv.index("--mount") + 1] == (
        "type=bind,src=/host/volume/scopes/c-100,dst=/work"
    )
    assert argv[argv.index("-w") + 1] == "/work"
    assert "--read-only" in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--network") + 1] == "none"
    assert "--user" in argv


async def test_shell_exec_reports_timeout(tmp_path: Path) -> None:
    box = service(tmp_path).new_workspace(Scope("user", 7))
    box.service.execute = AsyncMock(  # type: ignore[method-assign]
        return_value=SandboxResult("", "boom", True)
    )

    output = await invoke(tools(box)["shell_exec"], command="make")

    assert "Timed out" in output
    assert "boom" in output
