from __future__ import annotations

import asyncio
import io
import json
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, cast

import structlog
from agents.items import TResponseInputItem
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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
from .attachments import AttachmentService
from .config import MODELS, Reasoning, Settings
from .conversations import ConversationService
from .custom_agents import AGENT_CAPABILITIES, CustomAgentService
from .db import Database
from .group_context import GroupContextService
from .memory import MemoryService
from .models import AgentCapability, ChatSettings, ChatType, InstalledAgent, RequestContext, Scope
from .rich import RichMessages
from .runtime import AgentRuntime, RunOutput

log = structlog.get_logger()
REASONING: tuple[Reasoning, ...] = ("none", "low", "medium", "high", "xhigh", "max")
BOT_NAME = re.compile(r"(?<!\w)(?:skye|скай)(?!\w)", re.IGNORECASE)


class AgentWizard(StatesGroup):
    name = State()
    description = State()
    instructions = State()
    preview = State()


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
        custom_agents: CustomAgentService,
        groups: GroupContextService,
        attachments: AttachmentService,
        runtime: AgentRuntime,
    ) -> None:
        self.config = config
        self.bot = bot
        self.database = database
        self.access = access
        self.conversations = conversations
        self.memory = memory
        self.custom_agents = custom_agents
        self.groups = groups
        self.attachments = attachments
        self.runtime = runtime
        self.rich = RichMessages(bot)
        self.router = Router(name="skye")
        self._register()

    def _register(self) -> None:
        self.router.message.register(self.start, Command("start"))
        self.router.message.register(self.help, Command("help"))
        self.router.message.register(self.settings, Command("settings"))
        self.router.message.register(self.agents, Command("agents"))
        self.router.message.register(self.reset, Command("reset"))
        self.router.message.register(self.stop, Command("stop"))
        self.router.message.register(self.admin, Command("admin"))
        self.router.callback_query.register(self.settings_callback, F.data.startswith("settings:"))
        self.router.callback_query.register(self.agents_callback, F.data.startswith("agents:"))
        self.router.message.register(
            self.agent_wizard,
            StateFilter(
                AgentWizard.name,
                AgentWizard.description,
                AgentWizard.instructions,
                AgentWizard.preview,
            ),
        )
        self.router.message.register(self.chat)

    async def start(self, message: Message) -> None:
        context = self._context(message)
        if context is None:
            return
        if await self.access.allowed(context):
            parts = (message.text or "").split(maxsplit=1)
            if len(parts) == 2 and parts[1].startswith("agent_"):
                if not await self._can_edit(context):
                    await self.rich.send(
                        message, "Only chat administrators can install an agent here."
                    )
                    return
                try:
                    installed = await self.custom_agents.import_shared(
                        context.scope, parts[1], context.user_id
                    )
                except (LookupError, ValueError) as error:
                    await self.rich.send(message, str(error))
                    return
                await self.rich.send(
                    message,
                    f"Installed **{installed.version.name}** v{installed.version.version}. "
                    "Use /agents to select or inspect it.",
                )
                return
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
            "/settings — model, reasoning, agent, and memory\n\n"
            "/agents — create, install, select, and share agents\n\n"
            "/reset — new conversation\n\n"
            "/stop — cancel the active task"
        )

    async def settings(self, message: Message) -> None:
        context = self._context(message)
        if context is None or not await self._require_access(message, context):
            return
        current = await self.database.get_settings(context.scope)
        editable = await self._can_edit(context)
        agent_name = await self.custom_agents.active_name(
            context.scope, current.active_agent_id
        )
        await self.rich.send(
            message,
            self.rich.settings(current, agent_name),
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
        agent_name = await self.custom_agents.active_name(
            context.scope, current.active_agent_id
        )

        if action == ["settings", "models"]:
            await callback.message.edit_reply_markup(reply_markup=self._model_keyboard(current))
        elif action == ["settings", "reasoning"]:
            await callback.message.edit_reply_markup(reply_markup=self._reasoning_keyboard(current))
        elif action == ["settings", "agents"]:
            installed = await self.custom_agents.list(context.scope)
            await self.rich.edit(
                callback.message,
                self.rich.agents(installed, current.active_agent_id),
                reply_markup=self._agent_selection_keyboard(
                    installed, current.active_agent_id, editable, settings_back=True
                ),
            )
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
                self.rich.settings(current, agent_name),
                reply_markup=self._settings_keyboard(editable),
            )
        elif len(action) == 3 and action[:2] == ["settings", "model"]:
            if not editable or action[2] not in MODELS:
                await callback.answer("Only chat administrators can change this.", show_alert=True)
                return
            current = await self.database.set_model(context.scope, action[2])
            await self.rich.edit(
                callback.message,
                self.rich.settings(current, agent_name),
                reply_markup=self._settings_keyboard(True),
            )
        elif len(action) == 3 and action[:2] == ["settings", "reason"]:
            if not editable or action[2] not in REASONING:
                await callback.answer("Only chat administrators can change this.", show_alert=True)
                return
            current = await self.database.set_reasoning(context.scope, action[2])
            await self.rich.edit(
                callback.message,
                self.rich.settings(current, agent_name),
                reply_markup=self._settings_keyboard(True),
            )
        elif len(action) == 3 and action[:2] == ["settings", "agent"]:
            if not editable:
                await callback.answer("Only chat administrators can change this.", show_alert=True)
                return
            agent_id = None if action[2] == "skye" else action[2]
            try:
                await self.custom_agents.select(context.scope, agent_id)
            except (LookupError, ValueError) as error:
                await callback.answer(str(error), show_alert=True)
                return
            current = await self.database.get_settings(context.scope)
            agent_name = await self.custom_agents.active_name(
                context.scope, current.active_agent_id
            )
            await self.rich.edit(
                callback.message,
                self.rich.settings(current, agent_name),
                reply_markup=self._settings_keyboard(True),
            )
        await callback.answer()

    async def agents(self, message: Message, state: FSMContext) -> None:
        context = self._context(message)
        if context is None or not await self._require_access(message, context):
            return
        await state.clear()
        editable = await self._can_edit(context)
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) >= 2 and parts[1].lower() == "import":
            if not editable:
                await self.rich.send(
                    message, "Only chat administrators can install an agent here."
                )
                return
            if len(parts) != 3:
                await self.rich.send(message, "Use `/agents import <share link or token>`.")
                return
            try:
                installed = await self.custom_agents.import_shared(
                    context.scope, parts[2], context.user_id
                )
            except (LookupError, ValueError) as error:
                await self.rich.send(message, str(error))
                return
            await self.rich.send(
                message,
                f"Installed **{installed.version.name}** v{installed.version.version}.",
            )
        await self._send_agents(message, context, editable)

    async def agents_callback(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message or not isinstance(callback.message, Message) or not callback.data:
            await callback.answer()
            return
        context = self._context(callback.message, callback.from_user)
        if context is None or not await self.access.allowed(context):
            await callback.answer("Access denied", show_alert=True)
            return
        editable = await self._can_edit(context)
        action = callback.data.split(":")
        try:
            if action == ["agents", "list"]:
                await state.clear()
                await self._edit_agents(callback.message, context, editable)
            elif action == ["agents", "add"]:
                if not editable:
                    raise PermissionError("Only chat administrators can add agents here.")
                await state.set_state(AgentWizard.name)
                await state.set_data(
                    {"scope_kind": context.scope.kind, "scope_id": context.scope.id}
                )
                await self.rich.send(callback.message, "Send the agent name (up to 64 characters).")
            elif action == ["agents", "save"]:
                await self._save_agent(callback.message, context, state, editable)
            elif action == ["agents", "cancel"]:
                await state.clear()
                await self._edit_agents(callback.message, context, editable)
            elif len(action) == 3 and action[:2] == ["agents", "open"]:
                installed = await self.custom_agents.require_installed(context.scope, action[2])
                await self._show_agent(callback.message, context, installed, editable)
            elif len(action) == 3 and action[:2] == ["agents", "select"]:
                if not editable:
                    raise PermissionError("Only chat administrators can select an agent here.")
                agent_id = None if action[2] == "skye" else action[2]
                await self.custom_agents.select(context.scope, agent_id)
                await self._edit_agents(callback.message, context, editable)
            elif len(action) == 3 and action[:2] == ["agents", "edit"]:
                installed = await self.custom_agents.require_installed(context.scope, action[2])
                if installed.profile.owner_id != context.user_id:
                    raise PermissionError("Only the agent owner can edit it.")
                await state.set_state(AgentWizard.name)
                await state.set_data(
                    {
                        "scope_kind": context.scope.kind,
                        "scope_id": context.scope.id,
                        "agent_id": installed.profile.id,
                        "name": installed.version.name,
                        "description": installed.version.description,
                        "instructions": installed.version.instructions,
                        "model": installed.version.model,
                        "capabilities": list(installed.version.capabilities),
                    }
                )
                await self.rich.send(
                    callback.message,
                    f"Send a new name, or `.` to keep “{installed.version.name}”.",
                )
            elif len(action) == 3 and action[:2] == ["agents", "share"]:
                token = await self.custom_agents.share(context.scope, action[2], context.user_id)
                username = (await self.bot.me()).username
                if not username:
                    raise RuntimeError("The bot needs a username to create a share link.")
                await self.rich.send(
                    callback.message,
                    "Immutable share link for this version:\n\n"
                    f"[Install this agent](https://t.me/{username}?start=agent_{token})",
                )
            elif len(action) == 3 and action[:2] == ["agents", "remove"]:
                if not editable:
                    raise PermissionError("Only chat administrators can remove agents here.")
                await self.custom_agents.remove(context.scope, action[2])
                await self._edit_agents(callback.message, context, editable)
            elif len(action) == 3 and action[:2] == ["agents", "model"]:
                installed = await self.custom_agents.require_installed(context.scope, action[2])
                if installed.profile.owner_id != context.user_id:
                    raise PermissionError("Only the agent owner can edit it.")
                installed = await self.custom_agents.reconfigure(
                    agent_id=installed.profile.id,
                    owner_id=context.user_id,
                    scope=context.scope,
                    model=self.custom_agents.next_model(installed.version.model),
                )
                await self._show_agent(callback.message, context, installed, editable)
            elif len(action) == 4 and action[:2] == ["agents", "cap"]:
                installed = await self.custom_agents.require_installed(context.scope, action[2])
                capability = cast(AgentCapability, action[3])
                if capability not in AGENT_CAPABILITIES:
                    raise ValueError("Unknown capability.")
                if installed.profile.owner_id != context.user_id:
                    raise PermissionError("Only the agent owner can edit it.")
                selected = set(installed.version.capabilities)
                selected.symmetric_difference_update({capability})
                capabilities = tuple(item for item in AGENT_CAPABILITIES if item in selected)
                installed = await self.custom_agents.reconfigure(
                    agent_id=installed.profile.id,
                    owner_id=context.user_id,
                    scope=context.scope,
                    capabilities=capabilities,
                    keep_model=True,
                )
                await self._show_agent(callback.message, context, installed, editable)
        except (LookupError, PermissionError, ValueError) as error:
            await callback.answer(str(error), show_alert=True)
            return
        await callback.answer()

    async def agent_wizard(self, message: Message, state: FSMContext) -> None:
        context = self._context(message)
        if context is None or not await self._require_access(message, context):
            await state.clear()
            return
        data = await state.get_data()
        if (data.get("scope_kind"), data.get("scope_id")) != (
            context.scope.kind,
            context.scope.id,
        ) or not await self._can_edit(context):
            await state.clear()
            await self.rich.send(message, "This agent draft cannot be edited in this chat.")
            return
        current = await state.get_state()
        if current == AgentWizard.preview.state:
            await self.rich.send(message, "Use Save or Cancel below the preview.")
            return
        try:
            value = await self._agent_wizard_value(message, current)
        except ValueError as error:
            await self.rich.send(message, str(error))
            return
        keep = value == "." and "agent_id" in data
        if current == AgentWizard.name.state:
            if not keep and not 1 <= len(" ".join(value.split())) <= 64:
                await self.rich.send(message, "Agent name must be 1–64 characters.")
                return
            if not keep:
                await state.update_data(name=value)
            await state.set_state(AgentWizard.description)
            suffix = (
                f", or `.` to keep “{data['description']}”" if "agent_id" in data else ""
            )
            await self.rich.send(
                message, f"What is this agent good at? Send 1–240 characters{suffix}."
            )
        elif current == AgentWizard.description.state:
            if not keep and not 1 <= len(" ".join(value.split())) <= 240:
                await self.rich.send(message, "Description must be 1–240 characters.")
                return
            if not keep:
                await state.update_data(description=value)
            await state.set_state(AgentWizard.instructions)
            suffix = ", or `.` to keep the current instructions" if "agent_id" in data else ""
            await self.rich.send(
                message,
                "Send the agent instructions as text or a Markdown file" + suffix + ".",
            )
        elif current == AgentWizard.instructions.state:
            if not keep and not 1 <= len(value.strip()) <= 12_000:
                await self.rich.send(message, "Instructions must be 1–12,000 characters.")
                return
            if not keep:
                await state.update_data(instructions=value)
            await state.set_state(AgentWizard.preview)
            preview = await state.get_data()
            await self.rich.send(
                message,
                self.rich.agent_preview(
                    cast(str, preview["name"]),
                    cast(str, preview["description"]),
                    cast(str, preview["instructions"]),
                ),
                reply_markup=self._agent_preview_keyboard(),
            )

    async def _save_agent(
        self, message: Message, context: RequestContext, state: FSMContext, editable: bool
    ) -> None:
        if not editable or await state.get_state() != AgentWizard.preview.state:
            raise PermissionError("This agent draft is no longer active.")
        data = await state.get_data()
        if (data.get("scope_kind"), data.get("scope_id")) != (
            context.scope.kind,
            context.scope.id,
        ):
            raise PermissionError("This agent draft belongs to another chat.")
        if "agent_id" in data:
            await self.custom_agents.edit(
                agent_id=cast(str, data["agent_id"]),
                owner_id=context.user_id,
                scope=context.scope,
                name=cast(str, data["name"]),
                description=cast(str, data["description"]),
                instructions=cast(str, data["instructions"]),
                model=cast(Any, data.get("model")),
                capabilities=tuple(cast(list[AgentCapability], data["capabilities"])),
            )
        else:
            await self.custom_agents.create(
                owner_id=context.user_id,
                scope=context.scope,
                name=cast(str, data["name"]),
                description=cast(str, data["description"]),
                instructions=cast(str, data["instructions"]),
            )
        await state.clear()
        await self._edit_agents(message, context, editable)

    async def _agent_wizard_value(self, message: Message, state: str | None) -> str:
        if message.text:
            return message.text.strip()
        if state == AgentWizard.instructions.state and message.document:
            filename = message.document.file_name or ""
            if not filename.lower().endswith((".md", ".txt")):
                raise ValueError("Upload a Markdown or text file.")
            if message.document.file_size and message.document.file_size > 100_000:
                raise ValueError("The instructions file must be at most 100 KB.")
            destination = io.BytesIO()
            await self.bot.download(message.document, destination=destination)
            try:
                return destination.getvalue().decode("utf-8").strip()
            except UnicodeDecodeError:
                raise ValueError("The instructions file must be UTF-8.") from None
        raise ValueError("Send text for this step.")

    async def _send_agents(
        self, message: Message, context: RequestContext, editable: bool
    ) -> None:
        settings = await self.database.get_settings(context.scope)
        installed = await self.custom_agents.list(context.scope)
        await self.rich.send(
            message,
            self.rich.agents(installed, settings.active_agent_id),
            reply_markup=self._agents_keyboard(installed, settings.active_agent_id, editable),
        )

    async def _edit_agents(
        self, message: Message, context: RequestContext, editable: bool
    ) -> None:
        settings = await self.database.get_settings(context.scope)
        installed = await self.custom_agents.list(context.scope)
        await self.rich.edit(
            message,
            self.rich.agents(installed, settings.active_agent_id),
            reply_markup=self._agents_keyboard(installed, settings.active_agent_id, editable),
        )

    async def _show_agent(
        self,
        message: Message,
        context: RequestContext,
        installed: InstalledAgent,
        editable: bool,
    ) -> None:
        settings = await self.database.get_settings(context.scope)
        await self.rich.edit(
            message,
            self.rich.agent(installed, settings.active_agent_id == installed.profile.id),
            reply_markup=self._agent_keyboard(
                installed,
                settings.active_agent_id == installed.profile.id,
                editable,
                installed.profile.owner_id == context.user_id,
            ),
        )

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
        if context is None:
            return
        if context.chat_type != "private" and not await self._directed_at_bot(message):
            return
        if not await self._require_access(message, context):
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
        history_images = history.images if history else ()
        has_attachments = any(
            (source and (source.photo or source.voice or source.audio or source.document))
            for source in (message, message.reply_to_message)
        )
        if not has_attachments and not history_images:
            return text

        content: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
        await self.attachments.add(message, content)
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
        return bool(username and f"@{username.lower()}" in text.lower()) or bool(
            BOT_NAME.search(text)
        )

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
                [
                    InlineKeyboardButton(text="Agent", callback_data="settings:agents"),
                    InlineKeyboardButton(text="Memory", callback_data="settings:memory"),
                ],
            ]
        )

    @staticmethod
    def _agents_keyboard(
        agents: list[InstalledAgent], active_agent_id: str | None, editable: bool
    ) -> InlineKeyboardMarkup | None:
        rows = [
            [
                InlineKeyboardButton(
                    text=("✓ " if item.profile.id == active_agent_id else "")
                    + item.version.name,
                    callback_data=f"agents:open:{item.profile.id}",
                )
            ]
            for item in agents
        ]
        if editable:
            if active_agent_id is not None:
                rows.append(
                    [InlineKeyboardButton(text="Use Skye", callback_data="agents:select:skye")]
                )
            rows.append([InlineKeyboardButton(text="Add agent", callback_data="agents:add")])
        return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

    @staticmethod
    def _agent_selection_keyboard(
        agents: list[InstalledAgent],
        active_agent_id: str | None,
        editable: bool,
        *,
        settings_back: bool,
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        if editable:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=("✓ " if active_agent_id is None else "") + "Skye",
                        callback_data="settings:agent:skye",
                    )
                ]
            )
            rows.extend(
                [
                    InlineKeyboardButton(
                        text=("✓ " if item.profile.id == active_agent_id else "")
                        + item.version.name,
                        callback_data=f"settings:agent:{item.profile.id}",
                    )
                ]
                for item in agents
            )
        if settings_back:
            rows.append([InlineKeyboardButton(text="‹ Back", callback_data="settings:back")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def _agent_keyboard(
        installed: InstalledAgent, active: bool, editable: bool, owner: bool
    ) -> InlineKeyboardMarkup:
        agent_id = installed.profile.id
        rows: list[list[InlineKeyboardButton]] = []
        if editable and not active:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="Make active", callback_data=f"agents:select:{agent_id}"
                    )
                ]
            )
        if owner:
            model = MODELS[installed.version.model] if installed.version.model else "Chat default"
            rows.extend(
                [
                    [
                        InlineKeyboardButton(
                            text="Edit", callback_data=f"agents:edit:{agent_id}"
                        ),
                        InlineKeyboardButton(
                            text="Share", callback_data=f"agents:share:{agent_id}"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            text=f"Model: {model}", callback_data=f"agents:model:{agent_id}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=("✓ " if capability in installed.version.capabilities else "")
                            + capability.title(),
                            callback_data=f"agents:cap:{agent_id}:{capability}",
                        )
                        for capability in AGENT_CAPABILITIES
                    ],
                ]
            )
        if editable:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="Remove from chat", callback_data=f"agents:remove:{agent_id}"
                    )
                ]
            )
        rows.append([InlineKeyboardButton(text="‹ Back", callback_data="agents:list")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def _agent_preview_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Save", callback_data="agents:save"),
                    InlineKeyboardButton(text="Cancel", callback_data="agents:cancel"),
                ]
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
    BotCommand(command="settings", description="Model, agent, and memory"),
    BotCommand(command="agents", description="Create and manage agents"),
    BotCommand(command="reset", description="Start a new conversation"),
    BotCommand(command="stop", description="Stop the active task"),
    BotCommand(command="admin", description="Manage access (owner)"),
]
