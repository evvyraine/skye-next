from __future__ import annotations

import asyncio
from typing import Any

from aiogram.types import Message

from .config import Settings
from .db import Database
from .models import MediaGroupItem
from .telegram_threads import thread_id


class MediaGroupService:
    """Persist Telegram album members and resolve a complete album for one turn."""

    def __init__(self, config: Settings, database: Database) -> None:
        self.database = database
        self.settle_seconds = config.skye_media_group_settle_seconds

    async def capture(self, message: Message) -> None:
        item = self._item(message)
        if item is not None:
            await self.database.save_media_group_item(item)

    async def resolve(self, message: Message) -> tuple[MediaGroupItem, ...]:
        media_group_id = message.media_group_id
        if media_group_id is None and message.reply_to_message is not None:
            media_group_id = message.reply_to_message.media_group_id
            if media_group_id is None:
                media_group_id = await self.database.media_group_id_for_message(
                    message.chat.id, message.reply_to_message.message_id
                )
        if media_group_id is None:
            return ()
        await asyncio.sleep(self.settle_seconds)
        return tuple(await self.database.media_group_items(message.chat.id, media_group_id))

    async def claim(self, message: Message) -> bool:
        media_group_id = message.media_group_id
        if media_group_id is None:
            return True
        return await self.database.claim_media_group(
            message.chat.id, media_group_id, message.message_id
        )

    @staticmethod
    def _item(message: Message) -> MediaGroupItem | None:
        media_group_id = message.media_group_id
        if media_group_id is None:
            return None
        media_kind: str
        media: Any
        if message.photo:
            media_kind, media = "photo", message.photo[-1]
        else:
            media = None
            media_kind = ""
            for candidate in ("document", "audio", "video", "voice", "video_note"):
                value = getattr(message, candidate, None)
                if value is not None:
                    media_kind, media = candidate, value
                    break
            if media is None:
                return None
        return MediaGroupItem(
            chat_id=message.chat.id,
            media_group_id=media_group_id,
            message_id=message.message_id,
            thread_id=thread_id(message),
            media_kind=media_kind,
            file_id=media.file_id,
            file_unique_id=media.file_unique_id,
            file_name=getattr(media, "file_name", None),
            mime_type=getattr(media, "mime_type", None),
            file_size=getattr(media, "file_size", None),
            width=getattr(media, "width", None),
            height=getattr(media, "height", None),
            caption=message.caption,
            sent_at=int(message.date.timestamp()),
        )
