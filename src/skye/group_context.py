from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

import structlog
from aiogram import Bot
from aiogram.types import Message, User

from .config import Settings
from .db import Database
from .models import GroupMessage, Scope
from .telegram_threads import thread_id

log = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class GroupHistory:
    transcript: str
    images: tuple[tuple[int, str], ...]


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
        messages = await self.database.group_messages(
            message.chat.id,
            context_thread_id,
            before=message.message_id,
            limit=self.config.skye_group_context_messages,
        )
        if not messages:
            return GroupHistory("", ())

        transcript = "\n".join(self._line(item) for item in messages)
        photos: list[tuple[int, str]] = []
        if self.config.skye_group_context_images:
            photos = [
                (item.message_id, item.media_file_id)
                for item in messages
                if item.media_kind == "photo" and item.media_file_id
            ][-self.config.skye_group_context_images :]
        images: list[tuple[int, str]] = []
        for message_id, file_id in photos:
            try:
                destination = BytesIO()
                await self.bot.download(file_id, destination=destination)
                data = destination.getvalue()
                if len(data) <= self.config.skye_max_attachment_bytes:
                    encoded = base64.b64encode(data).decode()
                    images.append((message_id, f"data:image/jpeg;base64,{encoded}"))
            except Exception as error:
                log.warning("group_context_image_failed", error=type(error).__name__)
        return GroupHistory(transcript, tuple(images))

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

    @staticmethod
    def _line(message: GroupMessage) -> str:
        username = f" (@{message.sender_username})" if message.sender_username else ""
        sender_id = f" [id {message.sender_id}]" if message.sender_id is not None else ""
        sent_at = datetime.fromtimestamp(message.sent_at, UTC).strftime("%Y-%m-%d %H:%M UTC")
        reply = ""
        if message.reply_to_message_id:
            target = message.reply_sender_name or "message"
            target_username = (
                f" (@{message.reply_sender_username})" if message.reply_sender_username else ""
            )
            reply = f" replying to {target}{target_username} #{message.reply_to_message_id}"
            if message.reply_excerpt:
                reply += f" “{message.reply_excerpt}”"
        media = f" [{message.media_kind}]" if message.media_kind else ""
        return (
            f"#{message.message_id} · {sent_at} · "
            f"{message.sender_name}{username}{sender_id}{reply}{media}: "
            f"{message.text}"
        )
