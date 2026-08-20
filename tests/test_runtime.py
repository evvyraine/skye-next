import asyncio
import base64
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from agents import FunctionTool, ImageGenerationTool, ModelSettings, ShellTool, WebSearchTool
from openai import APIError, BadRequestError, RateLimitError

from skye.artifacts import GeneratedFile
from skye.config import SANDBOX_DOMAINS, Settings
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
    Skill,
)
from skye.runtime import (
    AgentRuntime,
    ContextLimitError,
    GuardedResponsesModel,
    StreamStartedError,
    TokenRateLimiter,
    is_transient,
    retry_after,
)


def config() -> Settings:
    return Settings(
        telegram_bot_token="123:token",
        openai_api_key="sk-test",
        skye_owner_ids="1",
        _env_file=None,
    )  # type: ignore[call-arg]


def sandbox_network_policy() -> dict[str, object]:
    return {"type": "allowlist", "allowed_domains": list(SANDBOX_DOMAINS)}


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
        "network_policy": sandbox_network_policy(),
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
    assert "can reach the public internet" in cast(str, with_shell.instructions)
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


def test_shell_mounts_uploaded_skills() -> None:
    memory = MemoryService(cast(Any, None))
    runtime = AgentRuntime(config(), cast(Any, None), memory, "You are Skye.")
    skill = Skill(
        "local1",
        Scope("user", 1),
        "skill_abc",
        "basic-math",
        "Add or multiply numbers.",
        "basic-math.zip",
        2,
        1,
        "now",
    )
    agent = runtime._agent(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
        skills=(skill,),
    )
    specialist = installed_agent("b2", "Coder", ("shell",))
    composed = runtime._agent(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False, active_agent_id="a1"),
        composition=AgentComposition(installed_agent("a1", "Researcher", ("web",)), (specialist,)),
        skills=(skill,),
    )

    shell = cast(ShellTool, agent.tools[2])
    assert shell.environment == {
        "type": "container_auto",
        "network_policy": sandbox_network_policy(),
        "skills": [{"type": "skill_reference", "skill_id": "skill_abc"}],
    }
    assert "basic-math" in cast(str, agent.instructions)
    nested = cast(Any, cast(FunctionTool, composed.tools[-1])._agent_instance)
    nested_shell = cast(ShellTool, nested.tools[0])
    assert nested_shell.environment is not None
    assert nested_shell.environment["skills"] == [
        {"type": "skill_reference", "skill_id": "skill_abc"}
    ]


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


class PartialStream(FakeStream):
    def __init__(self, event: object, error: Exception) -> None:
        super().__init__()
        self.event = event
        self.error = error

    async def stream_events(self) -> Any:
        yield self.event
        raise self.error


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

    assert is_transient(error)
    assert retry_after(error) == pytest.approx(4.628)


def test_retry_after_prefers_retry_after_header() -> None:
    error = rate_limit_error(
        "Rate limit reached. Please try again in 4.628s.",
        headers={"retry-after": "6"},
    )

    assert is_transient(error)
    assert retry_after(error) == pytest.approx(6.0)


def test_retry_after_waits_for_a_locked_conversation() -> None:
    error = conversation_locked_error()

    assert is_transient(error)
    assert retry_after(error) is None


def test_retry_after_ignores_permanent_errors() -> None:
    error = BadRequestError(
        "Invalid parameter",
        response=httpx.Response(400, request=_request()),
        body={"error": {"code": "invalid_request", "message": "Invalid parameter"}},
    )

    assert not is_transient(error)
    assert retry_after(error) is None


def test_retry_after_follows_the_cause_chain() -> None:
    error = RuntimeError("wrapper")
    error.__cause__ = rate_limit_error("Rate limit reached. Please try again in 2s.")

    assert is_transient(error)
    assert retry_after(error) == pytest.approx(2.0)


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
    assert delays[0] == pytest.approx(4.628)


async def test_run_does_not_retry_after_streaming_started() -> None:
    runtime = runtime_for_run()
    event = cast(
        Any,
        __import__("agents.stream_events", fromlist=["RawResponsesStreamEvent"]),
    ).RawResponsesStreamEvent(
        data=__import__(
            "openai.types.responses.response_text_delta_event",
            fromlist=["ResponseTextDeltaEvent"],
        ).ResponseTextDeltaEvent(
            content_index=0,
            delta="partial",
            item_id="msg_1",
            logprobs=[],
            output_index=0,
            sequence_number=1,
            type="response.output_text.delta",
        )
    )
    callback = AsyncMock()

    with (
        patch(
            "skye.runtime.Runner.run_streamed",
            side_effect=[
                PartialStream(event, rate_limit_error("Rate limit reached")),
                FakeStream(output="replacement"),
            ],
        ) as run_streamed,
        pytest.raises(StreamStartedError),
    ):
        await runtime.run(
            RequestContext(1, "private", 1),
            ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
            "hello",
            callback,
        )

    callback.assert_awaited_once_with("partial")
    assert run_streamed.call_count == 1


def test_model_settings_enable_compaction_and_disable_implicit_truncation() -> None:
    runtime = runtime_for_run()
    settings = runtime._model_settings(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
    )

    assert settings.context_management == [
        {"type": "compaction", "compact_threshold": 40_000}
    ]
    assert settings.truncation == "disabled"


async def test_token_rate_limiter_waits_for_the_rolling_budget() -> None:
    now = 0.0
    waits: list[float] = []

    def clock() -> float:
        return now

    async def sleep(seconds: float) -> None:
        nonlocal now
        waits.append(seconds)
        now += seconds

    limiter = TokenRateLimiter(100, 60.0, clock=clock, sleep=sleep)
    await limiter.acquire(60)
    await limiter.acquire(60)

    assert waits == [pytest.approx(60.0)]


async def test_guard_rejects_a_current_request_above_50k() -> None:
    client = AsyncMock()
    client.responses.input_tokens.count.side_effect = [
        type("Count", (), {"input_tokens": 80_000})(),
        type("Count", (), {"input_tokens": 51_000})(),
    ]
    limiter = AsyncMock()
    model = GuardedResponsesModel("gpt-5.6-luna", client, limiter, 50_000, 4_000)

    with pytest.raises(ContextLimitError):
        await model._admit("instructions", "large input", ModelSettings(), [], [], "conv_1")

    limiter.acquire.assert_not_awaited()


async def test_guard_reserves_50k_when_conversation_requires_compaction() -> None:
    client = AsyncMock()
    client.responses.input_tokens.count.side_effect = [
        type("Count", (), {"input_tokens": 80_000})(),
        type("Count", (), {"input_tokens": 2_000})(),
    ]
    limiter = AsyncMock()
    model = GuardedResponsesModel("gpt-5.6-luna", client, limiter, 50_000, 4_000)

    await model._admit("instructions", "hello", ModelSettings(), [], [], "conv_1")

    limiter.acquire.assert_awaited_once_with(54_000)


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
    assert delays[0] >= 1.0


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
