import base64
from typing import Any, cast

from agents import FunctionTool, ImageGenerationTool, ShellTool, WebSearchTool

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
from skye.runtime import AgentRuntime


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
