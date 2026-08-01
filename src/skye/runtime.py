from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from agents import (
    Agent,
    ImageGenerationTool,
    ModelSettings,
    Runner,
    ShellTool,
    Tool,
    WebSearchTool,
)
from agents.items import TResponseInputItem
from agents.result import RunResultStreaming
from agents.stream_events import RawResponsesStreamEvent
from openai.types.responses.response_text_delta_event import ResponseTextDeltaEvent

from .config import Settings
from .conversations import ConversationService
from .memory import MemoryService
from .models import ChatSettings, RequestContext

TextCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RunOutput:
    text: str
    images: tuple[bytes, ...]


class AgentRuntime:
    def __init__(
        self,
        config: Settings,
        conversations: ConversationService,
        memory: MemoryService,
        base_prompt: str,
    ) -> None:
        self.config = config
        self.conversations = conversations
        self.memory = memory
        self.base_prompt = base_prompt.strip()
        self._locks: defaultdict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._active: dict[tuple[int, int], RunResultStreaming] = {}

    async def run(
        self,
        context: RequestContext,
        settings: ChatSettings,
        user_input: str | list[TResponseInputItem],
        on_text: TextCallback,
    ) -> RunOutput:
        key = context.chat_id, context.thread_id
        async with self._locks[key]:
            conversation_id = await self.conversations.get_or_create(*key)
            memory_context = ""
            if settings.memory_enabled:
                memory_context = await self.memory.context(context.scope, self._query(user_input))
            agent = self._agent(context, settings, memory_context)
            result = Runner.run_streamed(
                agent,
                user_input,
                max_turns=self.config.skye_max_turns,
                conversation_id=conversation_id,
            )
            self._active[key] = result
            text = ""
            try:
                async with asyncio.timeout(self.config.skye_run_timeout_seconds):
                    async for event in result.stream_events():
                        if isinstance(event, RawResponsesStreamEvent) and isinstance(
                            event.data, ResponseTextDeltaEvent
                        ):
                            text += event.data.delta
                            await on_text(text)
            finally:
                self._active.pop(key, None)

            final = result.final_output if isinstance(result.final_output, str) else text
            return RunOutput(final.strip(), self._images(result))

    def stop(self, chat_id: int, thread_id: int) -> bool:
        result = self._active.get((chat_id, thread_id))
        if result is None:
            return False
        result.cancel()
        return True

    def _agent(
        self, context: RequestContext, settings: ChatSettings, memory_context: str = ""
    ) -> Agent[None]:
        instructions = self.base_prompt
        if context.chat_type != "private":
            instructions += (
                "\n\nYou are speaking in a Telegram group. Address the current sender when useful, "
                "and never reveal information from private conversations."
            )
        if settings.memory_enabled and memory_context:
            instructions += f"\n\n{memory_context}"
        if settings.memory_enabled:
            instructions += (
                "\n\nUse remember only for durable information explicitly stated or explicitly "
                "requested by the user. Never save secrets, transient requests, or inferred "
                "sensitive traits. Use recall when needed and forget when asked."
            )

        image_tool = ImageGenerationTool(
            tool_config={
                "type": "image_generation",
                "model": "gpt-image-2",
                "size": "auto",
                "quality": "auto",
                "output_format": "png",
                "background": "auto",
                "moderation": "auto",
                "partial_images": 1,
            }
        )
        shell_tool = ShellTool(
            environment=cast(
                Any,
                {
                    "type": "container_auto",
                    "network_policy": {"type": "disabled"},
                },
            )
        )
        safety_id = hmac.new(
            self.config.telegram_bot_token.encode(),
            str(context.user_id).encode(),
            hashlib.sha256,
        ).hexdigest()[:32]
        tools: list[Tool] = [
            WebSearchTool(search_context_size="medium"),
            image_tool,
            shell_tool,
        ]
        if settings.memory_enabled:
            tools.extend(self.memory.tools(context.scope))
        return Agent(
            name="Skye",
            instructions=instructions,
            model=settings.model,
            model_settings=ModelSettings(
                reasoning={"effort": settings.reasoning},
                verbosity="low",
                store=True,
                extra_body={"safety_identifier": safety_id},
            ),
            tools=tools,
        )

    @staticmethod
    def _query(user_input: str | list[TResponseInputItem]) -> str:
        if isinstance(user_input, str):
            return user_input
        texts: list[str] = []
        for item in user_input:
            for content in cast(Any, item).get("content", []):
                if content.get("type") == "input_text":
                    texts.append(content.get("text", ""))
        return " ".join(texts)

    @staticmethod
    def _images(result: RunResultStreaming) -> tuple[bytes, ...]:
        images: list[bytes] = []
        for response in result.raw_responses:
            for item in response.output:
                if getattr(item, "type", None) != "image_generation_call":
                    continue
                encoded = getattr(item, "result", None)
                if encoded:
                    images.append(base64.b64decode(encoded, validate=True))
        return tuple(images)
