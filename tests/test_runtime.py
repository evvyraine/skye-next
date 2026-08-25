import asyncio
import base64
from types import SimpleNamespace
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
    FALLBACK_EMPTY,
    AgentRuntime,
    ContextLimitError,
    GuardedResponsesModel,
    RunOutput,
    StreamStartedError,
    TokenRateLimiter,
    TurnDelivery,
    _dump_conversation_item,
    is_transient,
    leftover_reply,
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
        FunctionTool,
        FunctionTool,
    ]
    assert [cast(FunctionTool, tool).name for tool in agent.tools[3:]] == [
        "send_message",
        "send_voice",
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


def test_attached_files_are_mounted_in_the_hosted_sandbox() -> None:
    memory = MemoryService(cast(Any, None))
    runtime = AgentRuntime(config(), cast(Any, None), memory, "You are Skye.")
    agent = runtime._agent(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium"),
        input_file_ids=("file_1", "file_2"),
    )

    shell = cast(ShellTool, agent.tools[2])
    assert shell.environment == {
        "type": "container_auto",
        "network_policy": sandbox_network_policy(),
        "file_ids": ["file_1", "file_2"],
    }


def test_disabled_memory_is_not_injected_or_exposed() -> None:
    memory = MemoryService(cast(Any, None))
    runtime = AgentRuntime(config(), cast(Any, None), memory, "You are Skye.")
    agent = runtime._agent(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
        "secret memory",
    )

    assert len(agent.tools) == 5
    assert "secret memory" not in cast(str, agent.instructions)
    names = {
        cast(FunctionTool, tool).name for tool in agent.tools if isinstance(tool, FunctionTool)
    }
    assert "send_message" in names
    assert "send_voice" in names


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


def test_automation_tools_are_attached_when_managing() -> None:
    from skye.automations import AutomationService

    memory = MemoryService(cast(Any, None))
    automations = AutomationService(cast(Any, None), "https://chat.skye-bot.com")
    runtime = AgentRuntime(
        config(), cast(Any, None), memory, "You are Skye.", automations=automations
    )
    agent = runtime._agent(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium"),
        manage_automations=True,
    )
    names = [
        cast(FunctionTool, tool).name for tool in agent.tools if isinstance(tool, FunctionTool)
    ]
    assert "create_scheduled_automation" in names
    assert "create_webhook_automation" in names
    assert "You can create scheduled or webhook automations" in cast(str, agent.instructions)


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
    assert "includes message_id" in cast(str, group.instructions)
    assert "free-standing bubble" in cast(str, group.instructions)


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


def test_counting_tools_remove_streaming_only_image_options() -> None:
    tools = [
        {
            "type": "image_generation",
            "model": "gpt-image-2",
            "partial_images": 1,
        }
    ]

    counted = GuardedResponsesModel._counting_tools(cast(Any, tools))

    assert counted == [{"type": "image_generation", "model": "gpt-image-2"}]
    assert tools[0]["partial_images"] == 1


def test_counting_uploaded_file_uses_only_file_id() -> None:
    assert GuardedResponsesModel._counting_file_part(
        {"type": "input_file", "filename": "voice.ogg", "file_id": "file_voice"}
    ) == {"type": "input_file", "file_id": "file_voice"}


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
    assert agent.model == "gpt-5.6-luna"
    assert "Follow the Researcher method." in cast(str, agent.instructions)
    assert "You are Skye." not in cast(str, agent.instructions)
    assert "send_message and send_voice are your only ways" in cast(str, agent.instructions)
    assert isinstance(agent.tools[0], WebSearchTool)
    specialist_tool = cast(FunctionTool, agent.tools[-1])
    assert specialist_tool.name == "agent_b2"
    nested = cast(Any, specialist_tool)._agent_instance
    assert nested.name == "Coder"
    assert "You are Skye." not in cast(str, nested.instructions)
    assert "send_message and send_voice are your only ways" not in cast(
        str, nested.instructions
    )
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


class DeliveryThenFailure(FakeStream):
    def __init__(self, agent: Any, fail: bool) -> None:
        super().__init__()
        self.agent = agent
        self.fail = fail

    async def stream_events(self) -> Any:
        tool = next(
            cast(FunctionTool, item)
            for item in self.agent.tools
            if isinstance(item, FunctionTool) and item.name == "send_message"
        )
        yield SimpleNamespace(
            name="tool_called",
            item=SimpleNamespace(
                title=None,
                raw_item=SimpleNamespace(name="send_message", call_id="call_1"),
            ),
        )
        payload = '{"text":"Delivered once."}'
        await tool.on_invoke_tool(_tool_context("send_message", payload), payload)
        if self.fail:
            raise rate_limit_error("Please try again in 0.001s.")


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

    callback.assert_not_awaited()
    assert run_streamed.call_count == 1


def test_model_settings_enable_compaction_and_disable_implicit_truncation() -> None:
    runtime = runtime_for_run()
    settings = runtime._model_settings(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
    )

    assert settings.context_management == [{"type": "compaction", "compact_threshold": 40_000}]
    assert settings.truncation == "disabled"
    assert settings.extra_body["service_tier"] == "fast"


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
    client.conversations.items.list.return_value = type(
        "Page", (), {"data": [], "has_next_page": lambda self: False}
    )()
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
    client.conversations.items.list.return_value = type(
        "Page", (), {"data": [], "has_next_page": lambda self: False}
    )()
    client.responses.input_tokens.count.side_effect = [
        type("Count", (), {"input_tokens": 80_000})(),
        type("Count", (), {"input_tokens": 2_000})(),
    ]
    limiter = AsyncMock()
    model = GuardedResponsesModel("gpt-5.6-luna", client, limiter, 50_000, 4_000)

    await model._admit("instructions", "hello", ModelSettings(), [], [], "conv_1")

    limiter.acquire.assert_awaited_once_with(54_000)


async def test_guard_counts_a_snapshot_without_locking_the_conversation() -> None:
    client = AsyncMock()
    history = type(
        "Item",
        (),
        {"model_dump": lambda self, **_kwargs: {"role": "user", "content": "earlier"}},
    )()
    client.conversations.items.list.return_value = type(
        "Page", (), {"data": [history], "has_next_page": lambda self: False}
    )()
    client.responses.input_tokens.count.return_value = type("Count", (), {"input_tokens": 100})()
    limiter = AsyncMock()
    model = GuardedResponsesModel("gpt-5.6-luna", client, limiter, 50_000, 4_000)

    await model._admit("instructions", "now", ModelSettings(), [], [], "conv_1")

    kwargs = client.responses.input_tokens.count.await_args.kwargs
    assert "conversation" not in kwargs
    assert kwargs["input"] == [
        {"role": "user", "content": "earlier"},
        {"role": "user", "content": "now"},
    ]


async def test_guard_replaces_incomplete_images_in_conversation_snapshots() -> None:
    client = AsyncMock()
    history = type(
        "Item",
        (),
        {
            "model_dump": lambda self, **_kwargs: {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "look"},
                    {"type": "input_text", "text": "Attached image:"},
                    {"type": "input_image", "detail": "auto"},
                    {
                        "type": "input_image",
                        "file_id": "file_123",
                        "image_url": None,
                        "detail": "high",
                    },
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64,abc",
                        "detail": "auto",
                    },
                    {
                        "type": "input_file",
                        "filename": "notes.pdf",
                        "detail": "auto",
                    },
                ],
            }
        },
    )()
    client.conversations.items.list.return_value = type(
        "Page", (), {"data": [history], "has_next_page": lambda self: False}
    )()
    client.responses.input_tokens.count.return_value = type("Count", (), {"input_tokens": 100})()
    limiter = AsyncMock()
    model = GuardedResponsesModel("gpt-5.6-luna", client, limiter, 50_000, 4_000)

    await model._admit("instructions", "now", ModelSettings(), [], [], "conv_1")

    kwargs = client.responses.input_tokens.count.await_args.kwargs
    assert kwargs["input"][0]["content"] == [
        {"type": "input_text", "text": "look"},
        {"type": "input_text", "text": "Attached image:"},
        {"type": "input_text", "text": "[image]"},
        {"type": "input_image", "file_id": "file_123", "detail": "high"},
        {"type": "input_image", "image_url": "data:image/png;base64,abc", "detail": "auto"},
        {"type": "input_text", "text": "[notes.pdf]"},
    ]
    assert kwargs["input"][1] == {"role": "user", "content": "now"}


async def test_guard_drops_image_generation_action_from_conversation_snapshots() -> None:
    client = AsyncMock()
    history = type(
        "Item",
        (),
        {
            "model_dump": lambda self, **_kwargs: {
                "type": "image_generation_call",
                "id": "ig_1",
                "status": "completed",
                "action": "edit",
                "revised_prompt": "make it bluer",
            }
        },
    )()
    client.conversations.items.list.return_value = type(
        "Page", (), {"data": [history], "has_next_page": lambda self: False}
    )()
    client.responses.input_tokens.count.return_value = type("Count", (), {"input_tokens": 100})()
    limiter = AsyncMock()
    model = GuardedResponsesModel("gpt-5.6-luna", client, limiter, 50_000, 4_000)

    await model._admit("instructions", "now", ModelSettings(), [], [], "conv_1")

    kwargs = client.responses.input_tokens.count.await_args.kwargs
    assert kwargs["input"][0] == {
        "type": "image_generation_call",
        "id": "ig_1",
        "status": "completed",
    }
    assert "action" not in kwargs["input"][0]


def test_conversation_dump_keeps_only_declared_fields() -> None:
    class StoredCall:
        model_fields = {"id": object(), "status": object(), "type": object()}

        def model_dump(self, **_kwargs: object) -> dict[str, object]:
            return {
                "type": "image_generation_call",
                "id": "ig_1",
                "status": "completed",
                "action": "edit",
            }

    assert _dump_conversation_item(StoredCall()) == {
        "type": "image_generation_call",
        "id": "ig_1",
        "status": "completed",
    }


async def test_run_strips_sandbox_links_and_attaches_files() -> None:
    runtime = runtime_for_run()
    files = (GeneratedFile("notes.md", b"hi"),)
    stream = FakeStream(output="Done: [download notes.md](sandbox:/mnt/data/notes.md)")
    collect = AsyncMock(return_value=files)

    with (
        patch("skye.runtime.Runner.run_streamed", return_value=stream),
        patch("skye.runtime.collect_container_files", collect),
        patch("skye.runtime.time.time", return_value=1234.9),
    ):
        output = await runtime.run(
            RequestContext(1, "private", 1),
            ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
            "hello",
            AsyncMock(),
        )

    assert output.text == "Done: download notes.md"
    assert output.files == files
    assert collect.await_args.kwargs["created_after"] == 1234


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


async def test_run_does_not_retry_after_a_delivery_tool_started() -> None:
    runtime = runtime_for_run()
    delivered: list[str] = []
    attempts = 0

    async def on_reply(text: str, _reply_to: int | None = None) -> None:
        delivered.append(text)

    def run_streamed(agent: Any, *_args: Any, **_kwargs: Any) -> DeliveryThenFailure:
        nonlocal attempts
        attempts += 1
        return DeliveryThenFailure(agent, fail=attempts == 1)

    with (
        patch("skye.runtime.Runner.run_streamed", side_effect=run_streamed),
        pytest.raises(StreamStartedError),
    ):
        await runtime.run(
            RequestContext(1, "private", 1),
            ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
            "hello",
            AsyncMock(),
            on_reply=on_reply,
        )

    assert delivered == ["Delivered once."]
    assert attempts == 1


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


def test_usage_falls_back_to_a_conservative_length_estimate() -> None:
    from skye.runtime import _usage_value, estimate_usage_tokens

    assert estimate_usage_tokens("abcd", "ef") == 3
    assert estimate_usage_tokens("", "") == 1
    assert _usage_value(SimpleNamespace(input_tokens=10, output_tokens=5)) == 15
    assert _usage_value(SimpleNamespace(prompt_tokens=4, completion_tokens=6)) == 10
    assert _usage_value({"total_tokens": 8}) == 8
    assert _usage_value(None) is None


def _tool_context(name: str, payload: str) -> Any:
    from agents.tool_context import ToolContext

    return ToolContext(
        context=None,
        tool_name=name,
        tool_arguments=payload,
        tool_call_id="call_1",
        run_config=None,
    )


async def test_send_message_delivers_without_echoing_the_text() -> None:
    delivered: list[tuple[str, int | None]] = []

    async def on_reply(text: str, reply_to: int | None = None) -> None:
        delivered.append((text, reply_to))

    delivery = TurnDelivery(on_reply)
    tool = delivery.tool()
    first = await tool.on_invoke_tool(
        _tool_context("send_message", '{"text":"Hi."}'),
        '{"text":"Hi."}',
    )
    second = await tool.on_invoke_tool(
        _tool_context("send_message", '{"text":"Done."}'), '{"text":"Done."}'
    )

    assert first == "sent"
    assert second == "sent"
    assert delivered == [("Hi.", None), ("Done.", None)]
    assert delivery.sent == 2


async def test_send_message_passes_optional_reply_to() -> None:
    delivered: list[tuple[str, int | None]] = []

    async def on_reply(text: str, reply_to: int | None = None) -> None:
        delivered.append((text, reply_to))

    delivery = TurnDelivery(on_reply)
    tool = delivery.tool()
    quoted = await tool.on_invoke_tool(
        _tool_context("send_message", '{"text":"Quoted.","reply_to":123}'),
        '{"text":"Quoted.","reply_to":123}',
    )
    standalone = await tool.on_invoke_tool(
        _tool_context("send_message", '{"text":"Hi.","reply_to":null}'),
        '{"text":"Hi.","reply_to":null}',
    )
    invalid = await tool.on_invoke_tool(
        _tool_context("send_message", '{"text":"Nope.","reply_to":0}'),
        '{"text":"Nope.","reply_to":0}',
    )

    assert quoted == "sent"
    assert standalone == "sent"
    assert invalid == "sent"
    assert delivered == [("Quoted.", 123), ("Hi.", None), ("Nope.", None)]
    schema = tool.params_json_schema
    assert "reply_to" in schema["properties"]
    assert "text" in schema["required"]


async def test_send_message_caps_and_rejects_empty() -> None:
    delivered: list[str] = []

    async def on_reply(text: str, reply_to: int | None = None) -> None:
        delivered.append(text)

    delivery = TurnDelivery(on_reply, limit=2)
    tool = delivery.tool()
    await tool.on_invoke_tool(_tool_context("send_message", '{"text":"one"}'), '{"text":"one"}')
    await tool.on_invoke_tool(_tool_context("send_message", '{"text":"two"}'), '{"text":"two"}')
    capped = await tool.on_invoke_tool(
        _tool_context("send_message", '{"text":"three"}'), '{"text":"three"}'
    )
    empty = await tool.on_invoke_tool(
        _tool_context("send_message", '{"text":"  "}'),
        '{"text":"  "}',
    )

    assert capped == "Send limit reached for this turn."
    assert empty == "Nothing sent."
    assert delivered == ["one", "two"]
    assert delivery.sent == 2


async def test_send_voice_generates_nova_opus_with_model_instructions() -> None:
    delivered: list[tuple[bytes, int | None]] = []
    create = AsyncMock(return_value=SimpleNamespace(content=b"opus-audio"))
    client = SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(create=create)))

    async def on_voice(audio: bytes, reply_to: int | None = None) -> None:
        delivered.append((audio, reply_to))

    delivery = TurnDelivery(on_voice=on_voice, client=cast(Any, client))
    tool = delivery.voice_tool()
    payload = (
        '{"text":"Good morning.","instructions":"Warm, calm, and unhurried.",'
        '"reply_to":123}'
    )

    result = await tool.on_invoke_tool(_tool_context("send_voice", payload), payload)

    assert result == "sent"
    create.assert_awaited_once_with(
        model="gpt-4o-mini-tts",
        voice="nova",
        input="Good morning.",
        instructions="Warm, calm, and unhurried.",
        response_format="opus",
    )
    assert delivered == [(b"opus-audio", 123)]
    assert delivery.sent == 1
    schema = tool.params_json_schema
    assert set(schema["required"]) == {"text", "instructions", "reply_to"}
    assert "reply_to" in schema["properties"]


async def test_send_voice_validates_before_generating_audio() -> None:
    create = AsyncMock()
    client = SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(create=create)))

    async def on_voice(_audio: bytes, _reply_to: int | None = None) -> None:
        return

    delivery = TurnDelivery(on_voice=on_voice, client=cast(Any, client))

    assert await delivery.send_voice(" ", "Calm") == "Nothing sent."
    assert await delivery.send_voice("Hello", " ") == "Add voice delivery instructions."
    assert "too long" in await delivery.send_voice("x" * 4097, "Calm")
    create.assert_not_awaited()


async def test_run_keeps_inner_monologue_off_the_reply_callback() -> None:
    delivered: list[str] = []

    async def on_reply(text: str, reply_to: int | None = None) -> None:
        delivered.append(text)

    runtime = runtime_for_run()
    stream = FakeStream(output="HIDDEN leftover prose")
    with (
        patch("skye.runtime.Runner.run_streamed", return_value=stream),
        patch("skye.runtime.collect_container_files", AsyncMock(return_value=())),
    ):
        output = await runtime.run(
            RequestContext(1, "private", 1),
            ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
            "hello",
            AsyncMock(),
            on_reply=on_reply,
        )

    assert output.text == "HIDDEN leftover prose"
    assert output.sent == 0
    assert delivered == []
    assert leftover_reply(output, awaiting_reply=True) == "HIDDEN leftover prose"
    assert leftover_reply(output, awaiting_reply=False) is None


def test_leftover_reply_hides_prose_when_media_or_send_message_exists() -> None:
    text_only = RunOutput("inner thoughts", ())
    assert leftover_reply(text_only, awaiting_reply=True) == "inner thoughts"
    assert leftover_reply(RunOutput("", ()), awaiting_reply=True) == FALLBACK_EMPTY
    assert leftover_reply(RunOutput("inner", (), sent=2), awaiting_reply=True) is None
    assert leftover_reply(RunOutput("inner", (b"png",)), awaiting_reply=True) is None
    media = leftover_reply(
        RunOutput("inner", (), files=(GeneratedFile("a.md", b"x"),)),
        awaiting_reply=True,
    )
    assert media is None
    assert leftover_reply(text_only, awaiting_reply=False) is None


async def test_run_replaces_cited_tokens_from_web_search_annotations() -> None:
    token = "\ue200cite\ue202turn0view0\ue201"
    text = f"Paris is the capital. {token}"
    start = text.index(token)
    stream = FakeStream(output=text)
    stream.raw_responses = [
        SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="message",
                    content=[
                        SimpleNamespace(
                            type="output_text",
                            annotations=[
                                SimpleNamespace(
                                    type="url_citation",
                                    start_index=start,
                                    end_index=start + len(token),
                                    url="https://example.com/paris",
                                    title="Paris",
                                )
                            ],
                        )
                    ],
                )
            ]
        )
    ]
    runtime = runtime_for_run()
    with (
        patch("skye.runtime.Runner.run_streamed", return_value=stream),
        patch("skye.runtime.collect_container_files", AsyncMock(return_value=())),
    ):
        output = await runtime.run(
            RequestContext(1, "private", 1),
            ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
            "hello",
            AsyncMock(),
        )

    assert output.text == "Paris is the capital. [Paris](https://example.com/paris)"
    assert leftover_reply(output, awaiting_reply=True) == output.text
    assert "cite" not in output.text
    assert "turn0view0" not in output.text


def test_leftover_reply_strips_citation_tokens() -> None:
    token = "\ue200cite\ue202turn0view0\ue201"
    cleaned = leftover_reply(
        RunOutput(f"Paris is the capital. {token}", ()),
        awaiting_reply=True,
    )
    assert cleaned == "Paris is the capital."
    assert leftover_reply(RunOutput(token, ()), awaiting_reply=True) == FALLBACK_EMPTY
    surviving = leftover_reply(
        RunOutput(f"See https://example.com/report {token}", ()),
        awaiting_reply=True,
    )
    assert surviving == "See https://example.com/report"


async def test_send_message_sanitizes_citation_tokens() -> None:
    delivered: list[str] = []

    async def on_reply(text: str, reply_to: int | None = None) -> None:
        delivered.append(text)

    delivery = TurnDelivery(on_reply)
    tool = delivery.tool()
    token = "\ue200cite\ue202turn0view0\ue201"
    payload = '{"text":"Paris is the capital. ' + token + ' See https://example.com/report"}'
    result = await tool.on_invoke_tool(_tool_context("send_message", payload), payload)
    empty = await tool.on_invoke_tool(
        _tool_context("send_message", '{"text":"' + token + '"}'),
        '{"text":"' + token + '"}',
    )

    assert result == "sent"
    assert empty == "Nothing sent."
    assert delivered == ["Paris is the capital. See https://example.com/report"]
    assert "cite" not in delivered[0]
    assert "turn0view0" not in delivered[0]


async def test_hidden_turn_prompt_asks_skye_to_stay_quiet() -> None:
    memory = MemoryService(cast(Any, None))
    runtime = AgentRuntime(config(), cast(Any, None), memory, "You are Skye.")
    visible = runtime._agent(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
        awaiting_reply=True,
    )
    hidden = runtime._agent(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
        awaiting_reply=False,
    )
    assert "background automation" not in cast(str, visible.instructions)
    assert "background automation" in cast(str, hidden.instructions)
    assert "send_message and send_voice are your only ways" in cast(str, visible.instructions)
