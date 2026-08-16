import asyncio
import base64
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from agents import FunctionTool, ImageGenerationTool, ShellTool, WebSearchTool
from openai import APIError, BadRequestError, RateLimitError

from skye.artifacts import GeneratedFile
from skye.config import Settings
from skye.connectors import ConnectorTools
from skye.custom_agents import AgentComposition
from skye.memory import MemoryService
from skye.models import (
    AgentProfile,
    AgentVersion,
    ChatSettings,
    InstalledAgent,
    RequestContext,
    Scope,
)
from skye.runtime import AgentRuntime, retry_after


def config() -> Settings:
    return Settings(
        telegram_bot_token="123:token",
        openai_api_key="sk-test",
        skye_owner_ids="1",
        _env_file=None,
    )  # type: ignore[call-arg]


def test_agent_uses_only_hosted_capabilities() -> None:
    memory = MemoryService(cast(Any, None))
    runtime = AgentRuntime(config(), cast(Any, None), memory, "You are Skye.")
    agent = runtime._agent(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium"),
    )

    assert [type(tool) for tool in agent.tools] == [
        WebSearchTool,
        ImageGenerationTool,
        ShellTool,
        FunctionTool,
        FunctionTool,
        FunctionTool,
    ]
    assert [cast(FunctionTool, tool).name for tool in agent.tools[-3:]] == [
        "remember",
        "recall",
        "forget",
    ]
    shell = cast(ShellTool, agent.tools[2])
    assert shell.executor is None
    assert shell.environment == {
        "type": "container_auto",
        "network_policy": {"type": "disabled"},
    }


def test_disabled_memory_is_not_injected_or_exposed() -> None:
    memory = MemoryService(cast(Any, None))
    runtime = AgentRuntime(config(), cast(Any, None), memory, "You are Skye.")
    agent = runtime._agent(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
        "secret memory",
    )

    assert len(agent.tools) == 3
    assert "secret memory" not in cast(str, agent.instructions)


def test_memory_prompt_saves_what_the_user_wants() -> None:
    memory = MemoryService(cast(Any, None))
    runtime = AgentRuntime(config(), cast(Any, None), memory, "You are Skye.")
    agent = runtime._agent(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium"),
    )

    instructions = cast(str, agent.instructions)
    assert "anything the user wants saved" in instructions
    assert "without refusing or filtering" in instructions
    assert "Never save secrets" not in instructions


def test_connector_labels_are_private_context_only() -> None:
    memory = MemoryService(cast(Any, None))
    runtime = AgentRuntime(config(), cast(Any, None), memory, "You are Skye.")
    tools = ConnectorTools((), ("Gmail", "Work CRM"))

    private = runtime._agent(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
        connector_tools=tools,
    )
    group = runtime._agent(
        RequestContext(-100, "supergroup", 1),
        ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
        connector_tools=ConnectorTools((), ()),
    )

    assert "Gmail" in cast(str, private.instructions)
    assert "Work CRM" in cast(str, private.instructions)
    assert "Gmail" not in cast(str, group.instructions)


def test_generated_images_are_extracted() -> None:
    encoded = base64.b64encode(b"png").decode()
    result = cast(
        Any,
        type(
            "Result",
            (),
            {
                "raw_responses": [
                    type(
                        "Response",
                        (),
                        {
                            "output": [
                                type(
                                    "Image",
                                    (),
                                    {"type": "image_generation_call", "result": encoded},
                                )()
                            ]
                        },
                    )()
                ]
            },
        )(),
    )

    assert AgentRuntime._images(result) == (b"png",)


def test_shell_file_note_follows_capabilities() -> None:
    memory = MemoryService(cast(Any, None))
    runtime = AgentRuntime(config(), cast(Any, None), memory, "You are Skye.")
    with_shell = runtime._agent(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
    )
    without_shell = runtime._agent(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False, active_agent_id="a1"),
        composition=AgentComposition(installed_agent("a1", "Researcher", ("web",)), ()),
    )

    assert "/mnt/data" in cast(str, with_shell.instructions)
    assert "/mnt/data" not in cast(str, without_shell.instructions)


def installed_agent(
    agent_id: str, name: str, capabilities: tuple[str, ...], *, model: str | None = None
) -> InstalledAgent:
    return InstalledAgent(
        scope=Scope("user", 1),
        profile=AgentProfile(agent_id, 1, "private", 1, "now", "now"),
        version=AgentVersion(
            agent_id,
            1,
            name,
            f"{name} description",
            f"Follow the {name} method.",
            cast(Any, model),
            cast(Any, capabilities),
            "checksum",
            None,
            "now",
        ),
        enabled=True,
        installed_by=1,
        installed_at="now",
    )


def test_active_agent_and_specialist_are_composed() -> None:
    memory = MemoryService(cast(Any, None))
    runtime = AgentRuntime(config(), cast(Any, None), memory, "You are Skye.")
    active = installed_agent("a1", "Researcher", ("web",), model="gpt-5.6-sol")
    specialist = installed_agent("b2", "Coder", ("shell",))

    agent = runtime._agent(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium", active_agent_id="a1"),
        composition=AgentComposition(active, (specialist,)),
    )

    assert agent.name == "Researcher"
    assert agent.model == "gpt-5.6-sol"
    assert "Follow the Researcher method." in cast(str, agent.instructions)
    assert "You are Skye." not in cast(str, agent.instructions)
    assert isinstance(agent.tools[0], WebSearchTool)
    specialist_tool = cast(FunctionTool, agent.tools[-1])
    assert specialist_tool.name == "agent_b2"
    nested = cast(Any, specialist_tool)._agent_instance
    assert nested.name == "Coder"
    assert "You are Skye." not in cast(str, nested.instructions)
    assert isinstance(nested.tools[0], ShellTool)


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/responses")


def rate_limit_error(message: str, *, headers: dict[str, str] | None = None) -> RateLimitError:
    return RateLimitError(
        message,
        response=httpx.Response(429, request=_request(), headers=headers or {}),
        body=None,
    )


def conversation_locked_error() -> BadRequestError:
    return BadRequestError(
        "Error code: 400 - Another process is currently operating on this conversation.",
        response=httpx.Response(400, request=_request()),
        body={
            "error": {
                "message": "Another process is currently operating on this conversation. "
                "Please retry in a few seconds.",
                "type": "invalid_request_error",
                "param": "conversation",
                "code": "conversation_locked",
            }
        },
    )


class FakeStream:
    def __init__(self, error: Exception | None = None, output: str = "done") -> None:
        self._error = error
        self.final_output = output
        self.raw_responses: list[object] = []
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    async def stream_events(self) -> Any:
        if self._error:
            raise self._error
        if False:
            yield


def runtime_for_run() -> AgentRuntime:
    conversations = AsyncMock()
    conversations.get_or_create.return_value = "conv_1"
    return AgentRuntime(
        config(),
        conversations,
        MemoryService(cast(Any, None)),
        "You are Skye.",
    )


def test_retry_after_reads_tpm_wait_from_message() -> None:
    error = APIError(
        "Rate limit reached for gpt-5.6-luna in organization org-test on tokens per min "
        "(TPM): Limit 200000, Used 98472, Requested 116956. Please try again in 4.628s.",
        _request(),
        body=None,
    )

    assert retry_after(error) == pytest.approx(4.878)


def test_retry_after_prefers_retry_after_header() -> None:
    error = rate_limit_error(
        "Rate limit reached. Please try again in 4.628s.",
        headers={"retry-after": "6"},
    )

    assert retry_after(error) == pytest.approx(6.25)


def test_retry_after_waits_for_a_locked_conversation() -> None:
    assert retry_after(conversation_locked_error()) == pytest.approx(3.25)


def test_retry_after_ignores_permanent_errors() -> None:
    error = BadRequestError(
        "Invalid parameter",
        response=httpx.Response(400, request=_request()),
        body={"error": {"code": "invalid_request", "message": "Invalid parameter"}},
    )

    assert retry_after(error) is None


def test_retry_after_follows_the_cause_chain() -> None:
    error = RuntimeError("wrapper")
    error.__cause__ = rate_limit_error("Rate limit reached. Please try again in 2s.")

    assert retry_after(error) == pytest.approx(2.25)


async def test_run_retries_rate_limits_then_answers() -> None:
    runtime = runtime_for_run()
    delays: list[float] = []

    async def instant_delay(_active: object, seconds: float) -> None:
        delays.append(seconds)

    runtime._delay = instant_delay  # type: ignore[method-assign]
    streams = [
        FakeStream(
            error=APIError(
                "Rate limit reached on tokens per min (TPM). Please try again in 4.628s.",
                _request(),
                body=None,
            )
        ),
        FakeStream(output="Queued answer"),
    ]

    with patch("skye.runtime.Runner.run_streamed", side_effect=streams):
        output = await runtime.run(
            RequestContext(1, "private", 1),
            ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
            "hello",
            AsyncMock(),
        )

    assert output.text == "Queued answer"
    assert delays == pytest.approx([4.878])


async def test_run_strips_sandbox_links_and_attaches_files() -> None:
    runtime = runtime_for_run()
    files = (GeneratedFile("notes.md", b"hi"),)
    stream = FakeStream(output="Done: [download notes.md](sandbox:/mnt/data/notes.md)")

    with (
        patch("skye.runtime.Runner.run_streamed", return_value=stream),
        patch("skye.runtime.collect_container_files", AsyncMock(return_value=files)),
    ):
        output = await runtime.run(
            RequestContext(1, "private", 1),
            ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
            "hello",
            AsyncMock(),
        )

    assert output.text == "Done: download notes.md"
    assert output.files == files


async def test_run_retries_conversation_locks() -> None:
    runtime = runtime_for_run()
    delays: list[float] = []

    async def instant_delay(_active: object, seconds: float) -> None:
        delays.append(seconds)

    runtime._delay = instant_delay  # type: ignore[method-assign]
    streams = [FakeStream(error=conversation_locked_error()), FakeStream(output="Later")]

    with patch("skye.runtime.Runner.run_streamed", side_effect=streams):
        output = await runtime.run(
            RequestContext(1, "private", 1),
            ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
            "hello",
            AsyncMock(),
        )

    assert output.text == "Later"
    assert delays == pytest.approx([3.25])


async def test_run_does_not_retry_permanent_errors() -> None:
    runtime = runtime_for_run()
    error = BadRequestError(
        "Invalid parameter",
        response=httpx.Response(400, request=_request()),
        body={"error": {"code": "invalid_request"}},
    )

    with (
        patch("skye.runtime.Runner.run_streamed", return_value=FakeStream(error=error)),
        pytest.raises(BadRequestError),
    ):
        await runtime.run(
            RequestContext(1, "private", 1),
            ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
            "hello",
            AsyncMock(),
        )


async def test_stop_cancels_a_retry_wait() -> None:
    runtime = runtime_for_run()
    waiting = asyncio.Event()

    async def delay(active: Any, _seconds: float) -> None:
        waiting.set()
        await active.cancel.wait()
        raise asyncio.CancelledError

    runtime._delay = delay  # type: ignore[method-assign]
    with patch(
        "skye.runtime.Runner.run_streamed",
        return_value=FakeStream(error=rate_limit_error("Rate limit reached")),
    ):
        task = asyncio.create_task(
            runtime.run(
                RequestContext(1, "private", 1),
                ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
                "hello",
                AsyncMock(),
            )
        )
        await waiting.wait()
        assert runtime.stop(1, 0)
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_openai_runs_wait_in_one_queue() -> None:
    runtime = runtime_for_run()
    release = asyncio.Event()
    started = 0
    concurrent = 0
    max_concurrent = 0

    class BlockingStream(FakeStream):
        async def stream_events(self) -> Any:
            nonlocal started, concurrent, max_concurrent
            started += 1
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
            await release.wait()
            concurrent -= 1
            if False:
                yield

    with patch("skye.runtime.Runner.run_streamed", side_effect=lambda *_, **__: BlockingStream()):
        first = asyncio.create_task(
            runtime.run(
                RequestContext(1, "private", 1),
                ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
                "one",
                AsyncMock(),
            )
        )
        while started == 0:
            await asyncio.sleep(0)
        second = asyncio.create_task(
            runtime.run(
                RequestContext(2, "private", 2),
                ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
                "two",
                AsyncMock(),
            )
        )
        await asyncio.sleep(0.05)
        assert started == 1
        release.set()
        await asyncio.gather(first, second)

    assert started == 2
    assert max_concurrent == 1
