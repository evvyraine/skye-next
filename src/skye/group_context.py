from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiogram.types import Message, User

from .config import Settings
from .db import Database
from .models import GroupMessage, Scope
from .telegram_threads import thread_id


@dataclass(frozen=True, slots=True)
class GroupHistory:
    transcript: str


class GroupContextService:
    def __init__(self, config: Settings, database: Database, bot: Bot) -> None:
        self.config = config
        self.database = database
        self.bot = bot

    async def capture(self, message: Message) -> None:
        if message.chat.type not in {"group", "supergroup"}:
            return
        if await self.database.access_effect(Scope("chat", message.chat.id)) != "allow":
            return
        title = message.chat.title or message.chat.full_name or "Group"
        await self.database.remember_chat(message.chat.id, title)
        context_thread_id = thread_id(message)
        sender_id, sender_name, sender_username = self.sender(message)
        reply = message.reply_to_message
        media_kind, media_file_id = self._media(message)
        await self.database.save_group_message(
            GroupMessage(
                chat_id=message.chat.id,
                thread_id=context_thread_id,
                message_id=message.message_id,
                sender_id=sender_id,
                sender_name=sender_name,
                sender_username=sender_username,
                text=self.text(message),
                media_kind=media_kind,
                media_file_id=media_file_id,
                reply_to_message_id=reply.message_id if reply else None,
                reply_sender_name=self.sender(reply)[1] if reply else None,
                reply_sender_username=self.sender(reply)[2] if reply else None,
                reply_excerpt=self.text(reply)[:300] if reply else None,
                sent_at=int(message.date.timestamp()),
            )
        )
        await self.database.prune_group_messages(
            message.chat.id,
            context_thread_id,
            self.config.skye_group_context_messages + 1,
        )

    async def history(self, message: Message) -> GroupHistory:
        context_thread_id = thread_id(message)
        after = await self.database.conversation_context_message_id(
            message.chat.id, context_thread_id
        )
        messages = await self.database.group_messages(
            message.chat.id,
            context_thread_id,
            after=after,
            before=message.message_id,
            limit=self.config.skye_group_context_messages,
        )
        if not messages:
            return GroupHistory("")
        items: list[dict[str, Any]] = []
        for message_item in reversed(messages):
            candidate = [self._item(message_item), *items]
            rendered = self._json(candidate)
            if len(rendered) > self.config.skye_group_context_total_chars:
                break
            items = candidate
        return GroupHistory(self._json(items) if items else "")

    async def mark_seen(self, message: Message) -> None:
        await self.database.set_conversation_context_message_id(
            message.chat.id, thread_id(message), message.message_id
        )

    @staticmethod
    def sender(message: Message) -> tuple[int | None, str, str | None]:
        sender = message.from_user
        if sender:
            return sender.id, GroupContextService._user_name(sender), sender.username
        chat = message.sender_chat
        if chat:
            return chat.id, chat.title or chat.full_name, chat.username
        return None, "Unknown participant", None

    @staticmethod
    def _user_name(user: User) -> str:
        return " ".join(filter(None, (user.first_name, user.last_name))) or user.username or "User"

    @staticmethod
    def _media(message: Message) -> tuple[str | None, str | None]:
        if message.photo:
            return "photo", message.photo[-1].file_id
        for kind in ("animation", "audio", "document", "sticker", "video", "video_note", "voice"):
            media = getattr(message, kind, None)
            if media:
                return kind, media.file_id
        if message.location:
            return "location", None
        if message.poll:
            return "poll", None
        return None, None

    @staticmethod
    def describe(message: Message) -> str:
        if message.new_chat_members:
            names = ", ".join(
                GroupContextService._user_name(user) for user in message.new_chat_members
            )
            return f"[members joined: {names}]"
        if message.left_chat_member:
            return f"[member left: {GroupContextService._user_name(message.left_chat_member)}]"
        if message.new_chat_title:
            return f"[chat renamed to: {message.new_chat_title}]"
        if message.pinned_message:
            return f"[pinned message #{message.pinned_message.message_id}]"
        kind, _ = GroupContextService._media(message)
        if kind == "location" and message.location:
            return f"[location: {message.location.latitude}, {message.location.longitude}]"
        if kind == "poll" and message.poll:
            return f"[poll: {message.poll.question}]"
        if kind == "document" and message.document:
            return f"[document: {message.document.file_name or 'unnamed'}]"
        if kind == "audio" and message.audio:
            label = message.audio.title or message.audio.file_name or "audio"
            return f"[audio: {label}]"
        if kind == "sticker" and message.sticker:
            return f"[sticker: {message.sticker.emoji or 'no emoji'}]"
        return f"[{kind.replace('_', ' ') if kind else 'service message'}]"

    @classmethod
    def text(cls, message: Message) -> str:
        return (
            message.text
            or message.caption
            or cls._rich_text(message.rich_message)
            or cls.describe(message)
        )

    @classmethod
    def _rich_text(cls, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n".join(filter(None, (cls._rich_text(item) for item in value)))
        if hasattr(value, "model_dump"):
            value = value.model_dump(exclude_none=True)
        if not isinstance(value, dict):
            return ""
        content_keys = {"text", "caption", "summary", "credit", "label", "blocks", "items", "cells"}
        return "\n".join(
            filter(
                None,
                (cls._rich_text(item) for key, item in value.items() if key in content_keys),
            )
        )

    def _item(self, message: GroupMessage) -> dict[str, Any]:
        item: dict[str, Any] = {
            "message_id": message.message_id,
            "sent_at": datetime.fromtimestamp(message.sent_at, UTC).isoformat(),
            "sender": {
                "id": message.sender_id,
                "name": message.sender_name,
                "username": message.sender_username,
            },
            "text": self._truncate(
                message.text,
                self.config.skye_group_context_message_chars,
            ),
        }
        if message.media_kind:
            item["media"] = message.media_kind
        if message.reply_to_message_id:
            item["reply"] = {
                "message_id": message.reply_to_message_id,
                "sender_name": message.reply_sender_name,
                "sender_username": message.reply_sender_username,
                "excerpt": message.reply_excerpt,
            }
        return item

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    @staticmethod
    def _json(items: list[dict[str, Any]]) -> str:
        rendered = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        return rendered.replace("<", r"\u003c").replace(">", r"\u003e").replace("&", r"\u0026")
