import base64
from typing import Any, cast

from agents import ImageGenerationTool, ShellTool, WebSearchTool

from skye.config import Settings
from skye.models import ChatSettings, RequestContext
from skye.runtime import AgentRuntime


def config() -> Settings:
    return Settings(
        telegram_bot_token="123:token",
        openai_api_key="sk-test",
        skye_owner_ids="1",
        _env_file=None,
    )  # type: ignore[call-arg]


def test_agent_uses_only_hosted_capabilities() -> None:
    runtime = AgentRuntime(config(), cast(Any, None), "You are Skye.")
    agent = runtime._agent(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium"),
    )

    assert [type(tool) for tool in agent.tools] == [
        WebSearchTool,
        ImageGenerationTool,
        ShellTool,
    ]
    shell = cast(ShellTool, agent.tools[-1])
    assert shell.executor is None
    assert shell.environment == {
        "type": "container_auto",
        "network_policy": {"type": "disabled"},
    }


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
