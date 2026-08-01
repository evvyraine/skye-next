from __future__ import annotations

from collections.abc import Sequence

from aiogram import Bot
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputRichBlockParagraph,
    InputRichBlockSectionHeading,
    InputRichBlockTable,
    InputRichBlockThinking,
    InputRichBlockUnion,
    InputRichMessage,
    InputRichMessageMedia,
    Message,
    RichBlockTableCell,
)

from .config import MODELS
from .models import ChatSettings, InstalledAgent, Memory
from .telegram_threads import api_thread_id


class RichMessages:
    """The single boundary for every visible message Skye sends."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send(
        self,
        target: Message,
        content: str | InputRichMessage,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        return await self.bot.send_rich_message(
            chat_id=target.chat.id,
            message_thread_id=api_thread_id(target),
            rich_message=self._content(content),
            reply_markup=reply_markup,
        )

    async def edit(
        self,
        message: Message,
        content: str | InputRichMessage,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        await self.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=message.message_id,
            rich_message=self._content(content),
            reply_markup=reply_markup,
        )

    async def draft(self, target: Message, text: str | None = None) -> None:
        content = (
            InputRichMessage(markdown=text)
            if text
            else InputRichMessage(blocks=[InputRichBlockThinking(text="Thinking…")])
        )
        await self.bot.send_rich_message_draft(
            chat_id=target.chat.id,
            message_thread_id=api_thread_id(target),
            draft_id=target.message_id,
            rich_message=content,
        )

    @staticmethod
    def settings(settings: ChatSettings, agent_name: str = "Skye") -> InputRichMessage:
        def cell(text: str, *, header: bool = False) -> RichBlockTableCell:
            return RichBlockTableCell(
                text=text,
                is_header=header or None,
                align="left",
                valign="middle",
            )

        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text="Settings", size=2),
                InputRichBlockTable(
                    cells=[
                        [cell("Option", header=True), cell("Selected", header=True)],
                        [cell("Model"), cell(MODELS[settings.model])],
                        [cell("Reasoning"), cell(settings.reasoning.title())],
                        [cell("Agent"), cell(agent_name)],
                        [cell("Memory"), cell("On" if settings.memory_enabled else "Off")],
                    ],
                    is_bordered=True,
                    is_striped=True,
                ),
            ]
        )

    @staticmethod
    def agents(
        agents: Sequence[InstalledAgent], active_agent_id: str | None
    ) -> InputRichMessage:
        blocks: list[InputRichBlockUnion] = [
            InputRichBlockSectionHeading(text="Agents", size=2),
            InputRichBlockParagraph(
                text=(
                    "Skye is active. Installed agents remain available as specialists."
                    if active_agent_id is None
                    else "The selected agent leads this chat; the others remain specialists."
                )
            ),
        ]
        if not agents:
            blocks.append(InputRichBlockParagraph(text="No custom agents installed yet."))
            return InputRichMessage(blocks=blocks)

        def cell(text: str, *, header: bool = False) -> RichBlockTableCell:
            return RichBlockTableCell(
                text=text,
                is_header=header or None,
                align="left",
                valign="top",
            )

        rows = [
            [cell("Agent", header=True), cell("Version", header=True), cell("Role", header=True)]
        ]
        rows.extend(
            [
                cell(item.version.name),
                cell(str(item.version.version)),
                cell("Active" if item.profile.id == active_agent_id else "Specialist"),
            ]
            for item in agents
        )
        blocks.append(InputRichBlockTable(cells=rows, is_bordered=True, is_striped=True))
        return InputRichMessage(blocks=blocks)

    @staticmethod
    def agent(installed: InstalledAgent, active: bool) -> InputRichMessage:
        version = installed.version

        def cell(text: str) -> RichBlockTableCell:
            return RichBlockTableCell(text=text, align="left", valign="top")

        model = MODELS[version.model] if version.model else "Chat default"
        capabilities = ", ".join(item.title() for item in version.capabilities) or "None"
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text=version.name, size=2),
                InputRichBlockParagraph(text=version.description),
                InputRichBlockTable(
                    cells=[
                        [cell("Role"), cell("Active" if active else "Specialist")],
                        [cell("Version"), cell(str(version.version))],
                        [cell("Model"), cell(model)],
                        [cell("Capabilities"), cell(capabilities)],
                    ],
                    is_bordered=True,
                    is_striped=True,
                ),
                InputRichBlockSectionHeading(text="Instructions", size=3),
                InputRichBlockParagraph(text=version.instructions[:4000]),
            ]
        )

    @staticmethod
    def agent_preview(name: str, description: str, instructions: str) -> InputRichMessage:
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text=f"Preview · {name}", size=2),
                InputRichBlockParagraph(text=description),
                InputRichBlockSectionHeading(text="Instructions", size=3),
                InputRichBlockParagraph(text=instructions[:4000]),
            ]
        )

    @staticmethod
    def memory(memories: list[Memory], enabled: bool) -> InputRichMessage:
        heading = f"Memory · {'On' if enabled else 'Off'}"
        blocks: list[InputRichBlockUnion] = [
            InputRichBlockSectionHeading(text=heading, size=2)
        ]
        if not memories:
            blocks.append(InputRichBlockParagraph(text="No memories saved yet."))
            return InputRichMessage(blocks=blocks)

        def cell(text: str, *, header: bool = False) -> RichBlockTableCell:
            return RichBlockTableCell(
                text=text,
                is_header=header or None,
                align="left",
                valign="top",
            )

        rows = [[cell("ID", header=True), cell("Kind", header=True), cell("Memory", header=True)]]
        rows.extend(
            [cell(str(memory.id)), cell(memory.category.title()), cell(memory.content)]
            for memory in memories
        )
        blocks.append(InputRichBlockTable(cells=rows, is_bordered=True, is_striped=True))
        return InputRichMessage(blocks=blocks)

    @staticmethod
    def output(markdown: str, images: Sequence[bytes] = ()) -> InputRichMessage:
        media = [
            InputRichMessageMedia(
                id=f"image_{index}",
                media=InputMediaPhoto(
                    media=BufferedInputFile(image, filename=f"skye-{index}.png")
                ),
            )
            for index, image in enumerate(images, start=1)
        ]
        image_blocks = "\n\n".join(
            f"![Generated image {index}](tg://photo?id=image_{index})"
            for index in range(1, len(media) + 1)
        )
        body = "\n\n".join(part for part in (markdown.strip(), image_blocks) if part) or "Done."
        return InputRichMessage(markdown=body, media=media or None)

    @staticmethod
    def _content(content: str | InputRichMessage) -> InputRichMessage:
        if isinstance(content, InputRichMessage):
            return content
        return InputRichMessage(markdown=content)
