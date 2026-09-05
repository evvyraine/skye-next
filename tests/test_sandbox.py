import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from agents import FunctionTool

from skye.sandbox import (
    SandboxResult,
    SandboxService,
    SandboxUnavailableError,
    collect_outputs,
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


def service(tmp_path: Path) -> SandboxService:
    return SandboxService(
        "python:3.14-slim", 10, 1024, volume="test-sandbox-work", work_dir=tmp_path / "work"
    )


def test_seed_writes_safe_paths_only(tmp_path: Path) -> None:
    turn = service(tmp_path).new_turn()
    try:
        turn.seed([("report.txt", b"hi"), ("../evil.txt", b"no"), ("", b"empty")])

        assert (turn.workdir / "report.txt").read_bytes() == b"hi"
        assert not (turn.workdir / "evil.txt").exists()
    finally:
        turn.close()
    assert not turn.workdir.exists()


def test_collect_outputs_skips_hidden_and_oversized(tmp_path: Path) -> None:
    (tmp_path / ".hidden").write_bytes(b"x")
    (tmp_path / "big.bin").write_bytes(b"x" * 2048)
    (tmp_path / "ok.txt").write_bytes(b"ok")

    assert collect_outputs(tmp_path, 1024) == (("ok.txt", b"ok"),)


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
        await service(tmp_path).execute(tmp_path, "echo hi")


async def test_shell_exec_validates_commands(tmp_path: Path) -> None:
    turn = service(tmp_path).new_turn()
    try:
        (shell,) = turn.tools()
        assert isinstance(shell, FunctionTool)

        async def invoke(command: str) -> str:
            payload = json.dumps({"command": command})
            return await shell.on_invoke_tool(tool_context("shell_exec", payload), payload)

        assert await invoke("") == "Pass the command to run."
        assert await invoke("x" * 4001) == "That command is too long. Split it into smaller steps."
        with patch("skye.sandbox.shutil.which", return_value=None):
            assert await invoke("echo hi") == "Docker is not available."
    finally:
        turn.close()


async def test_execute_mounts_the_shared_volume_at_the_turn_subdir(tmp_path: Path) -> None:
    spawned: list[list[str]] = []

    async def fake_exec(*argv: str, **_kwargs: object) -> object:
        spawned.append(list(argv))

        class Process:
            async def communicate(self) -> tuple[bytes, bytes]:
                return b"out", b""

            def kill(self) -> None:
                return None

            async def wait(self) -> int:
                return 0

        return Process()

    turn = service(tmp_path).new_turn()
    try:
        with (
            patch("skye.sandbox.shutil.which", return_value="/usr/bin/docker"),
            patch("skye.sandbox.asyncio.create_subprocess_exec", side_effect=fake_exec),
        ):
            await turn.service.execute(turn.workdir, "echo hi")
    finally:
        turn.close()

    assert spawned
    argv = spawned[0]
    assert argv[argv.index("-v") + 1] == "test-sandbox-work:/work"
    assert argv[argv.index("-w") + 1].startswith("/work/turn-")
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"


async def test_shell_exec_reports_timeout_and_files(tmp_path: Path) -> None:
    turn = service(tmp_path).new_turn()
    try:
        turn.service.execute = AsyncMock(  # type: ignore[method-assign]
            return_value=SandboxResult("", "boom", True, (("a.txt", b"a"),))
        )
        (shell,) = turn.tools()

        output = await shell.on_invoke_tool(
            tool_context("shell_exec", '{"command":"make"}'),
            '{"command":"make"}',
        )

        assert "Timed out" in output
        assert "boom" in output
        assert "a.txt" in output
    finally:
        turn.close()
