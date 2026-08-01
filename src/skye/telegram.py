from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from io import BytesIO
from typing import Any, cast

import structlog
from agents.items import TResponseInputItem
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputRichMessage,
    Message,
    TelegramObject,
    Update,
    User,
)

from .access import AccessService
from .config import MODELS, Reasoning, Settings
from .conversations import ConversationService
from .db import Database
from .group_context import GroupContextService
from .memory import MemoryService
from .models import ChatSettings, ChatType, RequestContext, Scope
from .rich import RichMessages
from .runtime import AgentRuntime, RunOutput

log = structlog.get_logger()
REASONING: tuple[Reasoning, ...] = ("none", "low", "medium", "high", "xhigh", "max")


class UpdateMiddleware(BaseMiddleware):
    def __init__(self, database: Database, groups: GroupContextService) -> None:
        self.database = database
        self.groups = groups

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)
        payload = event.model_dump_json(exclude_none=True)
        if not await self.database.claim_update(event.update_id, payload):
            return None
        try:
            incoming = event.message or event.edited_message
            if incoming:
                await self.groups.capture(incoming)
            result = await handler(event, data)
        except Exception as error:
            await self.database.finish_update(event.update_id, type(error).__name__)
            raise
        await self.database.finish_update(event.update_id)
        return result


class TelegramApp:
    def __init__(
        self,
        config: Settings,
        bot: Bot,
        database: Database,
        access: AccessService,
        conversations: ConversationService,
        memory: MemoryService,
        groups: GroupContextService,
        runtime: AgentRuntime,
    ) -> None:
        self.config = config
        self.bot = bot
        self.database = database
        self.access = access
        self.conversations = conversations
        self.memory = memory
        self.groups = groups
        self.runtime = runtime
        self.rich = RichMessages(bot)
        self.router = Router(name="skye")
        self._register()

    def _register(self) -> None:
        self.router.message.register(self.start, Command("start"))
        self.router.message.register(self.help, Command("help"))
        self.router.message.register(self.settings, Command("settings"))
        self.router.message.register(self.reset, Command("reset"))
        self.router.message.register(self.stop, Command("stop"))
        self.router.message.register(self.admin, Command("admin"))
        self.router.callback_query.register(self.settings_callback, F.data.startswith("settings:"))
        self.router.message.register(self.chat)

    async def start(self, message: Message) -> None:
        context = self._context(message)
        if context is None:
            return
        if await self.access.allowed(context):
            await self.rich.send(
                message,
                "Hi. I'm Skye. Send a message, image, or task — "
                "I'll use the right tools when needed."
            )
        else:
            await self.rich.send(
                message, "This chat is not allowlisted yet. Ask the bot owner for access."
            )

    async def help(self, message: Message) -> None:
        await self.rich.send(
            message,
            "I can chat, search the web, work with images, "
            "and run code in an isolated container.\n\n"
            "/settings — model, reasoning, and memory\n\n"
            "/reset — new conversation\n\n"
            "/stop — cancel the active task"
        )

    async def settings(self, message: Message) -> None:
        context = self._context(message)
        if context is None or not await self._require_access(message, context):
            return
        current = await self.database.get_settings(context.scope)
        editable = await self._can_edit(context)
        await self.rich.send(
            message,
            self.rich.settings(current),
            reply_markup=self._settings_keyboard(editable),
        )

    async def settings_callback(self, callback: CallbackQuery) -> None:
        if not callback.message or not isinstance(callback.message, Message) or not callback.data:
            await callback.answer()
            return
        context = self._context(callback.message, callback.from_user)
        if context is None or not await self.access.allowed(context):
            await callback.answer("Access denied", show_alert=True)
            return
        editable = await self._can_edit(context)
        action = callback.data.split(":")
        current = await self.database.get_settings(context.scope)

        if action == ["settings", "models"]:
            await callback.message.edit_reply_markup(reply_markup=self._model_keyboard(current))
        elif action == ["settings", "reasoning"]:
            await callback.message.edit_reply_markup(reply_markup=self._reasoning_keyboard(current))
        elif action == ["settings", "memory"]:
            memories = await self.database.memories(context.scope, 10)
            await self.rich.edit(
                callback.message,
                self.rich.memory(memories, current.memory_enabled),
                reply_markup=self._memory_keyboard(current, bool(memories), editable),
            )
        elif action == ["settings", "memory", "toggle"]:
            if not editable:
                await callback.answer("Only chat administrators can change this.", show_alert=True)
                return
            current = await self.database.set_memory_enabled(
                context.scope, not current.memory_enabled
            )
            memories = await self.database.memories(context.scope, 10)
            await self.rich.edit(
                callback.message,
                self.rich.memory(memories, current.memory_enabled),
                reply_markup=self._memory_keyboard(current, bool(memories), True),
            )
        elif action == ["settings", "memory", "clear"]:
            if not editable:
                await callback.answer("Only chat administrators can change this.", show_alert=True)
                return
            await self.rich.edit(
                callback.message,
                "## Delete all memories?\n\n"
                "This cannot be undone. Conversation history is separate.",
                reply_markup=self._memory_clear_keyboard(),
            )
        elif action == ["settings", "memory", "confirm"]:
            if not editable:
                await callback.answer("Only chat administrators can change this.", show_alert=True)
                return
            await self.database.clear_memories(context.scope)
            await self.rich.edit(
                callback.message,
                self.rich.memory([], current.memory_enabled),
                reply_markup=self._memory_keyboard(current, False, True),
            )
        elif action == ["settings", "back"]:
            await self.rich.edit(
                callback.message,
                self.rich.settings(current),
                reply_markup=self._settings_keyboard(editable),
            )
        elif len(action) == 3 and action[:2] == ["settings", "model"]:
            if not editable or action[2] not in MODELS:
                await callback.answer("Only chat administrators can change this.", show_alert=True)
                return
            current = await self.database.set_model(context.scope, action[2])
            await self.rich.edit(
                callback.message,
                self.rich.settings(current),
                reply_markup=self._settings_keyboard(True),
            )
        elif len(action) == 3 and action[:2] == ["settings", "reason"]:
            if not editable or action[2] not in REASONING:
                await callback.answer("Only chat administrators can change this.", show_alert=True)
                return
            current = await self.database.set_reasoning(context.scope, action[2])
            await self.rich.edit(
                callback.message,
                self.rich.settings(current),
                reply_markup=self._settings_keyboard(True),
            )
        await callback.answer()

    async def reset(self, message: Message) -> None:
        context = self._context(message)
        if context is None or not await self._require_access(message, context):
            return
        await self.conversations.reset(context.chat_id, context.thread_id)
        await self.rich.send(message, "Conversation reset. Long-term memory was not changed.")

    async def stop(self, message: Message) -> None:
        context = self._context(message)
        if context is None or not await self._require_access(message, context):
            return
        stopped = self.runtime.stop(context.chat_id, context.thread_id)
        await self.rich.send(message, "Stopping…" if stopped else "Nothing is running here.")

    async def admin(self, message: Message) -> None:
        context = self._context(message)
        if context is None or not self.access.is_owner(context.user_id):
            await self.rich.send(message, "This command is only available to the bot owner.")
            return
        parts = (message.text or "").split()
        if len(parts) == 1:
            await self.rich.send(
                message,
                "`/admin allow [id]`\n\n`/admin ban <id>`\n\n"
                "`/admin remove <id>`\n\n`/admin list`\n\n"
                "Run /admin allow in a group to allow the whole group."
            )
            return
        action = parts[1].lower()
        if action == "list":
            entries = await self.database.list_access()
            text = "\n".join(
                f"- {entry['effect']} {entry['kind']} `{entry['telegram_id']}`"
                for entry in entries
            )
            await self.rich.send(message, text or "The allowlist is empty.")
            return
        target = self._admin_target(message, parts[2] if len(parts) > 2 else None)
        if target is None:
            await self.rich.send(
                message,
                "Provide a numeric id, reply to a user, or run this inside a group."
            )
            return
        if action in {"allow", "ban"}:
            await self.database.set_access(target, cast(Any, action), context.user_id)
            await self.rich.send(message, f"{action.title()}ed {target.kind} {target.id}.")
        elif action == "remove":
            removed = await self.database.remove_access(target)
            await self.rich.send(
                message, "Access entry removed." if removed else "No matching entry."
            )
        else:
            await self.rich.send(message, "Unknown admin action.")

    async def chat(self, message: Message) -> None:
        context = self._context(message)
        if context is None or not await self._require_access(message, context):
            return
        if context.chat_type != "private" and not await self._directed_at_bot(message):
            return
        try:
            user_input = await self._input(message, context)
        except ValueError as error:
            await self.rich.send(message, str(error))
            return
        current = await self.database.get_settings(context.scope)
        placeholder: Message | None = None
        if context.chat_type == "private":
            await self.rich.draft(message)
        else:
            placeholder = await self.rich.send(message, "Thinking…")
        last_edit = 0.0

        async def on_text(text: str) -> None:
            nonlocal last_edit
            now = time.monotonic()
            if now - last_edit < 0.8:
                return
            last_edit = now
            with suppress(TelegramBadRequest):
                if context.chat_type == "private":
                    await self.rich.draft(message, text[:32000])
                elif placeholder:
                    await self.rich.edit(placeholder, text[:32000] or "Thinking…")

        try:
            output = await self.runtime.run(context, current, user_input, on_text)
            await self.groups.advance(context, message.message_id)
            await self._deliver(message, placeholder, output)
        except TimeoutError:
            await self._finish(message, placeholder, "This took too long, so I stopped it.")
        except asyncio.CancelledError:
            await self._finish(message, placeholder, "Stopped.")
        except Exception as error:
            log.exception(
                "agent_run_failed",
                chat_id=context.chat_id,
                thread_id=context.thread_id,
                error=type(error).__name__,
            )
            await self._finish(message, placeholder, "Something went wrong. Please try again.")

    async def _input(
        self, message: Message, context: RequestContext
    ) -> str | list[TResponseInputItem]:
        text = message.text or message.caption or self.groups.describe(message)
        if context.chat_type != "private":
            identity = context.display_name
            if context.username:
                identity += f" (@{context.username})"
            identity += f" [id {context.user_id}]"
            reply = message.reply_to_message
            reply_context = ""
            if reply:
                reply_id, reply_name, reply_username = self.groups.sender(reply)
                reply_identity = reply_name + (f" (@{reply_username})" if reply_username else "")
                if reply_id is not None:
                    reply_identity += f" [id {reply_id}]"
                excerpt = reply.text or reply.caption or self.groups.describe(reply)
                reply_context = (
                    f"\nReplying to {reply_identity} #{reply.message_id}: {excerpt[:500]}"
                )
            history = await self.groups.history(message)
            group_context = (
                "<recent_group_context>\n"
                f"{history.transcript}\n"
                "</recent_group_context>\n\n"
                if history.transcript
                else ""
            )
            text = (
                f"{group_context}<current_message>\n"
                f"{identity}: {text}{reply_context}\n"
                "</current_message>"
            )
        else:
            history = None
        photos = list(message.photo or [])[-1:]
        reply = message.reply_to_message
        if reply and reply.photo:
            photos.extend(list(reply.photo)[-1:])
        history_images = history.images if history else ()
        if not photos and not history_images:
            return text

        content: list[dict[str, str]] = [{"type": "input_text", "text": text}]
        seen: set[str] = set()
        for photo in photos:
            if photo.file_unique_id in seen:
                continue
            seen.add(photo.file_unique_id)
            if photo.file_size and photo.file_size > self.config.skye_max_attachment_bytes:
                raise ValueError("That image is too large.")
            destination = BytesIO()
            await self.bot.download(photo, destination=destination)
            encoded = base64.b64encode(destination.getvalue()).decode()
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{encoded}",
                    "detail": "auto",
                }
            )
        for message_id, image_url in history_images:
            content.append({"type": "input_text", "text": f"Image from message #{message_id}:"})
            content.append({"type": "input_image", "image_url": image_url, "detail": "auto"})
        return cast(list[TResponseInputItem], [{"role": "user", "content": content}])

    async def _deliver(
        self, target: Message, placeholder: Message | None, output: RunOutput
    ) -> None:
        chunks = self._chunks(output.text)
        chunks = chunks or [""]
        first = self.rich.output(chunks[0], output.images)
        await self._finish(target, placeholder, first)
        for chunk in chunks[1:]:
            await self.rich.send(target, self.rich.output(chunk))

    async def _finish(
        self, target: Message, placeholder: Message | None, content: str | InputRichMessage
    ) -> None:
        if placeholder:
            await self.rich.edit(placeholder, content)
        else:
            await self.rich.send(target, content)

    async def _require_access(self, message: Message, context: RequestContext) -> bool:
        if await self.access.allowed(context):
            return True
        await self.rich.send(message, "This chat is not allowlisted.")
        return False

    async def _can_edit(self, context: RequestContext) -> bool:
        if context.chat_type == "private" or self.access.is_owner(context.user_id):
            return True
        member = await self.bot.get_chat_member(context.chat_id, context.user_id)
        return member.status in {"administrator", "creator"}

    async def _directed_at_bot(self, message: Message) -> bool:
        if (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == self.bot.id
        ):
            return True
        username = (await self.bot.me()).username
        text = message.text or message.caption or ""
        return bool(username and f"@{username.lower()}" in text.lower())

    @staticmethod
    def _context(message: Message, user: User | None = None) -> RequestContext | None:
        sender = user or message.from_user
        if sender is None or message.chat.type == "channel":
            return None
        name = " ".join(part for part in (sender.first_name, sender.last_name) if part)
        return RequestContext(
            chat_id=message.chat.id,
            chat_type=cast(ChatType, message.chat.type),
            user_id=sender.id,
            thread_id=message.message_thread_id or 0,
            username=sender.username,
            display_name=name or sender.username or "User",
        )

    @staticmethod
    def _admin_target(message: Message, raw_id: str | None) -> Scope | None:
        if message.reply_to_message and message.reply_to_message.from_user:
            return Scope("user", message.reply_to_message.from_user.id)
        if raw_id:
            try:
                telegram_id = int(raw_id)
            except ValueError:
                return None
            return Scope("chat" if telegram_id < 0 else "user", telegram_id)
        if message.chat.type in {"group", "supergroup"}:
            return Scope("chat", message.chat.id)
        return None

    @staticmethod
    def _settings_keyboard(editable: bool) -> InlineKeyboardMarkup | None:
        if not editable:
            return None
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Model", callback_data="settings:models"),
                    InlineKeyboardButton(text="Reasoning", callback_data="settings:reasoning"),
                ],
                [InlineKeyboardButton(text="Memory", callback_data="settings:memory")],
            ]
        )

    @staticmethod
    def _memory_keyboard(
        settings: ChatSettings, has_memories: bool, editable: bool
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        if editable:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="Turn off" if settings.memory_enabled else "Turn on",
                        callback_data="settings:memory:toggle",
                    )
                ]
            )
            if has_memories:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text="Delete all", callback_data="settings:memory:clear"
                        )
                    ]
                )
        rows.append([InlineKeyboardButton(text="‹ Back", callback_data="settings:back")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def _memory_clear_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Delete all", callback_data="settings:memory:confirm"
                    ),
                    InlineKeyboardButton(text="Cancel", callback_data="settings:memory"),
                ]
            ]
        )

    @staticmethod
    def _model_keyboard(settings: ChatSettings) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton(
                    text=("✓ " if model == settings.model else "") + label,
                    callback_data=f"settings:model:{model}",
                )
            ]
            for model, label in MODELS.items()
        ]
        rows.append([InlineKeyboardButton(text="‹ Back", callback_data="settings:back")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def _reasoning_keyboard(settings: ChatSettings) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton(
                    text=("✓ " if effort == settings.reasoning else "") + effort.title(),
                    callback_data=f"settings:reason:{effort}",
                )
            ]
            for effort in REASONING
        ]
        rows.append([InlineKeyboardButton(text="‹ Back", callback_data="settings:back")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def _chunks(text: str, limit: int = 32000) -> list[str]:
        text = text.strip()
        if not text:
            return []
        chunks: list[str] = []
        while len(text) > limit:
            split = text.rfind("\n", 0, limit)
            if split < limit // 2:
                split = text.rfind(" ", 0, limit)
            if split < limit // 2:
                split = limit
            chunks.append(text[:split].rstrip())
            text = text[split:].lstrip()
        if text:
            chunks.append(text)
        return chunks


async def replay_pending(dispatcher: Dispatcher, bot: Bot, database: Database) -> None:
    for payload in await database.pending_updates():
        update = Update.model_validate(json.loads(payload), context={"bot": bot})
        await dispatcher.feed_update(bot, update)


COMMANDS = [
    BotCommand(command="start", description="Start Skye"),
    BotCommand(command="help", description="Show capabilities"),
    BotCommand(command="settings", description="Model and reasoning"),
    BotCommand(command="reset", description="Start a new conversation"),
    BotCommand(command="stop", description="Stop the active task"),
    BotCommand(command="admin", description="Manage access (owner)"),
]
