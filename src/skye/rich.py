from __future__ import annotations

from collections.abc import Sequence

from aiogram import Bot
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputRichBlockSectionHeading,
    InputRichBlockTable,
    InputRichBlockThinking,
    InputRichMessage,
    InputRichMessageMedia,
    Message,
    RichBlockTableCell,
)

from .config import MODELS
from .models import ChatSettings


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
            message_thread_id=target.message_thread_id,
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
            message_thread_id=target.message_thread_id,
            draft_id=target.message_id,
            rich_message=content,
        )

    @staticmethod
    def settings(settings: ChatSettings) -> InputRichMessage:
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
                    ],
                    is_bordered=True,
                    is_striped=True,
                ),
            ]
        )

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
