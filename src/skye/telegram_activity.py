from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

TelegramChatAction = Literal[
    "typing",
    "upload_photo",
    "record_video",
    "upload_video",
    "record_voice",
    "upload_voice",
    "upload_document",
    "choose_sticker",
    "find_location",
    "record_video_note",
    "upload_video_note",
]

CHAT_ACTION_REFRESH_SECONDS = 4.0


@dataclass(slots=True)
class TelegramActivity:
    """Keep one native Telegram activity status alive for an interactive turn."""

    bot: Bot | None
    chat_id: int
    thread_id: int = 0
    refresh_seconds: float = CHAT_ACTION_REFRESH_SECONDS
    action: TelegramChatAction = "typing"
    enabled: bool = True
    _wake: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False)
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def __aenter__(self) -> TelegramActivity:
        if not self.enabled or self.bot is None:
            return self
        await self.pulse()
        self._task = asyncio.create_task(self._refresh())
        return self

    async def __aexit__(self, *_error: object) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def show(self, action: TelegramChatAction) -> None:
        if action == self.action:
            return
        self.action = action
        if not self.enabled or self.bot is None:
            return
        self._wake.set()
        await self.pulse()

    async def send(self, action: TelegramChatAction) -> None:
        """Show an upload status immediately, even if Telegram cleared the same action."""
        self.action = action
        if not self.enabled or self.bot is None:
            return
        self._wake.set()
        await self.pulse()

    async def pulse(self) -> None:
        """Send the current status now; failures never interrupt the real response."""
        bot = self.bot
        if not self.enabled or bot is None:
            return
        try:
            async with self._send_lock:
                await bot.send_chat_action(
                    chat_id=self.chat_id,
                    message_thread_id=self.thread_id or None,
                    action=self.action,
                )
        except TelegramAPIError:
            return

    async def _refresh(self) -> None:
        while True:
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.refresh_seconds)
            except TimeoutError:
                await self.pulse()
