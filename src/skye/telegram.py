from __future__ import annotations

import asyncio
import io
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from typing import Any, Literal, cast

import structlog
from agents.items import TResponseInputItem
from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.client.default import Default
from aiogram.dispatcher.event.bases import UNHANDLED
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
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
    RichTextUnion,
    TelegramObject,
    Update,
    User,
)

from .access import AccessService
from .attachments import AttachmentService
from .billing import AccountPanel, BillingError, BillingService
from .config import Reasoning, Settings
from .connectors import ConnectorError, ConnectorPanel, ConnectorService, ConnectorWizard
from .conversations import ConversationService
from .custom_agents import AGENT_CAPABILITIES, CustomAgentService
from .db import Database
from .group_context import GroupContextService
from .media_groups import MediaGroupService
from .memory import MemoryService
from .models import (
    AccessEffect,
    AccessEntry,
    AgentCapability,
    ChatSettings,
    ChatType,
    InstalledAgent,
    MediaGroupItem,
    RequestContext,
    Scope,
    ScopeKind,
)
from .quota import AllowanceError, QuotaService
from .rich import RichMessages
from .runtime import AgentRuntime, ContextLimitError, RunOutput, StreamStartedError
from .skills import SkillError, SkillPanel, SkillService, SkillWizard
from .telegram_projects import (
    PROJECT_KEYBOARD_CATCHUP,
    PROJECT_KEYBOARD_PROJECTS,
    ProjectPanel,
    ProjectWizard,
    TelegramProjectError,
    TelegramProjectService,
    project_reply_keyboard,
)
from .telegram_threads import thread_id

log = structlog.get_logger()
REASONING: tuple[Reasoning, ...] = ("none", "low", "medium", "high", "xhigh", "max")
BOT_NAME = re.compile(r"(?<!\w)(?:skye|скай)(?!\w)", re.IGNORECASE)
GROUP_NEEDS_PLUS = "Group mode needs an admin with Skye Plus. Open /account in a private chat."
AdminAction = Literal["allow", "ban", "remove"]
CATCHUP_PROMPT = (
    "Catch me up on this conversation: where things stand, open threads, and decisions. "
    "Be concise. Do not start new work."
)


class AgentWizard(StatesGroup):
    name = State()
    description = State()
    instructions = State()
    preview = State()


class AdminPrompt(StatesGroup):
    target = State()


class UpdateMiddleware(BaseMiddleware):
    def __init__(
        self, database: Database, groups: GroupContextService, media_groups: MediaGroupService
    ) -> None:
        self.database = database
        self.groups = groups
        self.media_groups = media_groups

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)
        payload = dump_update(event)
        if not await self.database.claim_update(event.update_id, payload):
            return None
        try:
            incoming = event.message or event.edited_message
            if incoming:
                await self.media_groups.capture(incoming)
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
        connectors: ConnectorService,
        groups: GroupContextService,
        media_groups: MediaGroupService,
        attachments: AttachmentService,
        runtime: AgentRuntime,
        skills: SkillService,
        telegram_projects: TelegramProjectService,
        billing: BillingService,
    ) -> None:
        self.config = config
        self.bot = bot
        self.database = database
        self.access = access
        self.conversations = conversations
        self.memory = memory
        self.custom_agents = custom_agents
        self.connector_service = connectors
        self.groups = groups
        self.media_groups = media_groups
        self.attachments = attachments
        self.runtime = runtime
        self.skill_service = skills
        self.telegram_projects = telegram_projects
        self.billing = billing
        self.quota = QuotaService(database, billing, access)
        self.rich = RichMessages(bot)
        self.connectors = ConnectorPanel(connectors, self.rich, bot)
        self.skill_panel = SkillPanel(skills, self.rich, bot)
        self.project_panel = ProjectPanel(telegram_projects, self.rich, bot)
        self.account = AccountPanel(billing, access, self.rich, bot)
        self.router = Router(name="skye")
        self._register()

    def _register(self) -> None:
        self.router.message.register(self.start, Command("start"))
        self.router.message.register(self.help, Command("help"))
        self.router.message.register(self.account_command, Command("account"))
        self.router.message.register(self.paysupport, Command("paysupport"))
        self.router.message.register(self.terms, Command("terms"))
        self.router.message.register(self.settings, Command("settings"))
        self.router.message.register(self.projects, Command("projects"))
        self.router.message.register(
            self.projects, F.chat.type == "private", F.text == PROJECT_KEYBOARD_PROJECTS
        )
        self.router.message.register(self.agents, Command("agents"))
        self.router.message.register(self.catchup, Command("catchup"))
        self.router.message.register(
            self.catchup, F.chat.type == "private", F.text == PROJECT_KEYBOARD_CATCHUP
        )
        self.router.message.register(self.reset, Command("reset"))
        self.router.message.register(self.stop, Command("stop"))
        self.router.message.register(self.admin, Command("admin"))
        self.router.callback_query.register(self.settings_callback, F.data.startswith("settings:"))
        self.router.callback_query.register(self.account_callback, F.data.startswith("acct:"))
        self.router.callback_query.register(self.projects_callback, F.data.startswith("proj:"))
        self.router.callback_query.register(self.connectors_callback, F.data.startswith("conn:"))
        self.router.callback_query.register(self.skills_callback, F.data.startswith("skill:"))
        self.router.callback_query.register(self.agents_callback, F.data.startswith("agents:"))
        self.router.callback_query.register(self.admin_callback, F.data.startswith("admin:"))
        self.router.message.register(
            self.agent_wizard,
            StateFilter(
                AgentWizard.name,
                AgentWizard.description,
                AgentWizard.instructions,
                AgentWizard.preview,
            ),
        )
        self.router.message.register(
            self.connector_wizard,
            StateFilter(
                ConnectorWizard.search,
                ConnectorWizard.mcp_name,
                ConnectorWizard.mcp_url,
                ConnectorWizard.mcp_headers,
                ConnectorWizard.edit,
            ),
        )
        self.router.message.register(self.skill_wizard, StateFilter(SkillWizard.upload))
        self.router.message.register(
            self.project_wizard,
            StateFilter(
                ProjectWizard.name,
                ProjectWizard.emoji,
                ProjectWizard.instructions,
                ProjectWizard.edit,
            ),
        )
        self.router.message.register(self.admin_prompt, StateFilter(AdminPrompt.target))
        self.router.pre_checkout_query.register(self.pre_checkout)
        self.router.message.register(self.successful_payment, F.successful_payment)
        self.router.message.register(self.refunded_payment, F.refunded_payment)
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
                    self.rich.agent_installed(
                        installed.version.name, installed.version.version, hint=True
                    ),
                )
                return
            await self.rich.send(
                message,
                "Hi. I'm Skye. Send a message, image, or task — "
                "I'll use the right tools when needed.",
                reply_markup=await self._private_reply_keyboard(context),
            )
        else:
            await self._deny_access(message, context)

    async def help(self, message: Message) -> None:
        await self.rich.send(
            message,
            "I can chat, search the web, work with images, "
            "run code in an isolated container, and use apps and skills you add.\n\n"
            "/account — Skye plans, Telegram Stars, and cancellation\n\n"
            "/settings — reasoning, agent, memory, connectors, and skills\n\n"
            "/projects — switch and manage project chats\n\n"
            "/agents — create, install, select, and share agents\n\n"
            "/catchup — summarize this conversation\n\n"
            "/reset — new conversation\n\n"
            "/stop — cancel the active task\n\n"
            "A few useful links.",
            reply_markup=self._help_keyboard(),
        )

    async def account_command(self, message: Message) -> None:
        context = self._context(message)
        if context is None:
            return
        await self.account.show(message, context)

    async def paysupport(self, message: Message) -> None:
        await self.account.paysupport(message)

    async def terms(self, message: Message) -> None:
        await self.account.terms(message)

    async def account_callback(self, callback: CallbackQuery) -> None:
        if not callback.message or not isinstance(callback.message, Message) or not callback.data:
            await callback.answer()
            return
        context = self._context(callback.message, callback.from_user)
        if context is None:
            await callback.answer()
            return
        try:
            await self.account.handle_callback(
                callback.message, context, callback.data.split(":")[1:]
            )
        except BillingError as error:
            await callback.answer(str(error), show_alert=True)
            return
        await callback.answer()

    async def pre_checkout(self, query: PreCheckoutQuery) -> None:
        await self.account.pre_checkout(query)

    async def successful_payment(self, message: Message) -> None:
        context = self._context(message)
        if context is None:
            return
        await self.account.successful_payment(message, context)

    async def refunded_payment(self, message: Message) -> None:
        context = self._context(message)
        if context is None:
            return
        await self.account.refunded_payment(message, context)

    async def settings(self, message: Message) -> None:
        context = self._context(message)
        if context is None or not await self._require_access(message, context):
            return
        current = await self.database.get_settings(context.scope)
        editable = await self._can_edit(context)
        agent_name = await self.custom_agents.active_name(context.scope, current.active_agent_id)
        private = context.chat_type == "private"
        connector_count, skill_count = await self._settings_counts(context, private)
        await self.rich.send(
            message,
            self.rich.settings(
                current, agent_name, connector_count=connector_count, skill_count=skill_count
            ),
            reply_markup=self._settings_keyboard(editable, private=private),
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
        agent_name = await self.custom_agents.active_name(context.scope, current.active_agent_id)

        if action == ["settings", "reasoning"]:
            await self.rich.edit(
                callback.message,
                self.rich.choose_reasoning(current.reasoning),
                reply_markup=self._reasoning_keyboard(current),
            )
        elif action == ["settings", "agents"]:
            installed = await self.custom_agents.list(context.scope)
            await self.rich.edit(
                callback.message,
                self.rich.agents(installed, current.active_agent_id),
                reply_markup=self._agent_selection_keyboard(
                    installed, current.active_agent_id, editable, settings_back=True
                ),
            )
        elif action == ["settings", "connectors"]:
            try:
                await self.connectors.show_home(callback.message, context, editable=editable)
            except ConnectorError as error:
                await callback.answer(str(error), show_alert=True)
                return
        elif action == ["settings", "skills"]:
            await self.skill_panel.show_home(callback.message, context, editable=editable)
        elif action == ["settings", "projects"]:
            if context.chat_type != "private":
                await callback.answer("Projects are available in a private chat.", show_alert=True)
                return
            await self.project_panel.show_home(callback.message, context, from_settings=True)
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
                self.rich.memory_clear_confirm(),
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
            await self._edit_settings(callback.message, context, current, agent_name, editable)
        elif len(action) == 3 and action[:2] == ["settings", "reason"]:
            if not editable or action[2] not in REASONING:
                await callback.answer("Only chat administrators can change this.", show_alert=True)
                return
            current = await self.database.set_reasoning(context.scope, action[2])
            await self._edit_settings(callback.message, context, current, agent_name, True)
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
            await self._edit_settings(callback.message, context, current, agent_name, True)
        await callback.answer()

    async def connectors_callback(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message or not isinstance(callback.message, Message) or not callback.data:
            await callback.answer()
            return
        context = self._context(callback.message, callback.from_user)
        if context is None or not await self.access.allowed(context):
            await callback.answer("Access denied", show_alert=True)
            return
        action = callback.data.split(":")[1:]
        editable = await self._can_edit(context)
        try:
            await self.connectors.handle_callback(
                callback.message, context, action, state, editable=editable
            )
        except (ConnectorError, PermissionError, LookupError) as error:
            await callback.answer(str(error), show_alert=True)
            return
        await callback.answer()

    async def skills_callback(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message or not isinstance(callback.message, Message) or not callback.data:
            await callback.answer()
            return
        context = self._context(callback.message, callback.from_user)
        if context is None or not await self.access.allowed(context):
            await callback.answer("Access denied", show_alert=True)
            return
        action = callback.data.split(":")[1:]
        editable = await self._can_edit(context)
        try:
            await self.skill_panel.handle_callback(
                callback.message, context, action, state, editable=editable
            )
        except (SkillError, PermissionError, LookupError) as error:
            await callback.answer(str(error), show_alert=True)
            return
        await callback.answer()

    async def skill_wizard(self, message: Message, state: FSMContext) -> None:
        context = self._context(message)
        if context is None or not await self._require_access(message, context):
            await state.clear()
            return
        if not await self._can_edit(context):
            await state.clear()
            await self.rich.send(message, "Only chat administrators can add skills here.")
            return
        try:
            await self.skill_panel.handle_wizard(message, context, state)
        except SkillError as error:
            await self.rich.send(message, str(error))

    async def connector_wizard(self, message: Message, state: FSMContext) -> None:
        context = self._context(message)
        if context is None or not await self._require_access(message, context):
            await state.clear()
            return
        await self.connectors.handle_wizard(message, context, state)

    async def _edit_settings(
        self,
        message: Message,
        context: RequestContext,
        current: ChatSettings,
        agent_name: str,
        editable: bool,
    ) -> None:
        private = context.chat_type == "private"
        connector_count, skill_count = await self._settings_counts(context, private)
        await self.rich.edit(
            message,
            self.rich.settings(
                current, agent_name, connector_count=connector_count, skill_count=skill_count
            ),
            reply_markup=self._settings_keyboard(editable, private=private),
        )

    async def _settings_counts(self, context: RequestContext, private: bool) -> tuple[int, int]:
        connector_count = (
            await self.connector_service.connected_count(context.user_id)
            if private
            else await self.connector_service.group_share_count(context.chat_id)
        )
        skill_count = len(await self.skill_service.list(context.scope))
        return connector_count, skill_count

    async def agents(self, message: Message, state: FSMContext) -> None:
        context = self._context(message)
        if context is None or not await self._require_access(message, context):
            return
        await state.clear()
        editable = await self._can_edit(context)
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) >= 2 and parts[1].lower() == "import":
            if not editable:
                await self.rich.send(message, "Only chat administrators can install an agent here.")
                return
            if len(parts) != 3:
                await self.rich.send(message, self.rich.agent_import_usage())
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
                self.rich.agent_installed(installed.version.name, installed.version.version),
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
                if not await self._require_plus(callback.message, context):
                    await callback.answer()
                    return
                await state.set_state(AgentWizard.name)
                await state.set_data(
                    {"scope_kind": context.scope.kind, "scope_id": context.scope.id}
                )
                await self.rich.send(callback.message, self.rich.agent_name_prompt())
            elif action == ["agents", "save"]:
                if not await self._require_plus(callback.message, context):
                    await callback.answer()
                    return
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
                if not await self._require_plus(callback.message, context):
                    await callback.answer()
                    return
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
                    self.rich.agent_name_prompt(installed.version.name),
                )
            elif len(action) == 3 and action[:2] == ["agents", "share"]:
                if not await self._require_plus(callback.message, context):
                    await callback.answer()
                    return
                token = await self.custom_agents.share(context.scope, action[2], context.user_id)
                username = (await self.bot.me()).username
                if not username:
                    raise RuntimeError("The bot needs a username to create a share link.")
                await self.rich.send(
                    callback.message,
                    self.rich.agent_share_link(f"https://t.me/{username}?start=agent_{token}"),
                )
            elif len(action) == 3 and action[:2] == ["agents", "remove"]:
                if not editable:
                    raise PermissionError("Only chat administrators can remove agents here.")
                await self.custom_agents.remove(context.scope, action[2])
                await self._edit_agents(callback.message, context, editable)
            elif len(action) == 4 and action[:2] == ["agents", "cap"]:
                installed = await self.custom_agents.require_installed(context.scope, action[2])
                capability = cast(AgentCapability, action[3])
                if capability not in AGENT_CAPABILITIES:
                    raise ValueError("Unknown capability.")
                if installed.profile.owner_id != context.user_id:
                    raise PermissionError("Only the agent owner can edit it.")
                if not await self._require_plus(callback.message, context):
                    await callback.answer()
                    return
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
        if not await self._require_plus(message, context):
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
            keep_description = cast(str, data["description"]) if "agent_id" in data else None
            await self.rich.send(message, self.rich.agent_description_prompt(keep_description))
        elif current == AgentWizard.description.state:
            if not keep and not 1 <= len(" ".join(value.split())) <= 240:
                await self.rich.send(message, "Description must be 1–240 characters.")
                return
            if not keep:
                await state.update_data(description=value)
            await state.set_state(AgentWizard.instructions)
            await self.rich.send(
                message, self.rich.agent_instructions_prompt(keep="agent_id" in data)
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

    async def _send_agents(self, message: Message, context: RequestContext, editable: bool) -> None:
        settings = await self.database.get_settings(context.scope)
        installed = await self.custom_agents.list(context.scope)
        await self.rich.send(
            message,
            self.rich.agents(installed, settings.active_agent_id),
            reply_markup=self._agents_keyboard(installed, settings.active_agent_id, editable),
        )

    async def _edit_agents(self, message: Message, context: RequestContext, editable: bool) -> None:
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

    async def projects(self, message: Message, state: FSMContext) -> None:
        context = self._context(message)
        if context is None or not await self._require_access(message, context):
            return
        if context.chat_type != "private":
            await self.rich.send(message, "Projects are available in a private chat.")
            return
        await state.clear()
        await self.project_panel.show_home(message, context, edit=False)

    async def projects_callback(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message or not isinstance(callback.message, Message) or not callback.data:
            await callback.answer()
            return
        context = self._context(callback.message, callback.from_user)
        if context is None or not await self.access.allowed(context):
            await callback.answer("Access denied", show_alert=True)
            return
        if context.chat_type != "private":
            await callback.answer("Projects are available in a private chat.", show_alert=True)
            return
        action = callback.data.split(":")[1:]
        try:
            await self.project_panel.handle_callback(
                callback.message,
                context,
                action,
                state,
                catchup=self._panel_catchup,
            )
        except (TelegramProjectError, PermissionError, LookupError, ValueError) as error:
            await callback.answer(str(error), show_alert=True)
            return
        await callback.answer()

    async def project_wizard(self, message: Message, state: FSMContext) -> None:
        context = self._context(message)
        if context is None or not await self._require_access(message, context):
            await state.clear()
            return
        if context.chat_type != "private":
            await state.clear()
            await self.rich.send(message, "Projects are available in a private chat.")
            return
        try:
            await self.project_panel.handle_wizard(message, context, state)
        except TelegramProjectError as error:
            await self.rich.send(message, str(error))

    async def catchup(self, message: Message, state: FSMContext) -> None:
        context = self._context(message)
        if context is None or not await self._require_access(message, context):
            return
        await state.clear()
        await self._run_catchup(message, context)

    async def _panel_catchup(
        self, message: Message, context: RequestContext, project_id: str
    ) -> None:
        project = await self.telegram_projects.require(context.user_id, project_id)
        await self._run_catchup(
            message,
            context,
            conversation_id=project.openai_conversation_id,
            extra_instructions=project.instructions,
            draft=False,
            resolved=True,
        )

    async def _run_catchup(
        self,
        message: Message,
        context: RequestContext,
        *,
        conversation_id: str | None = None,
        extra_instructions: str | None = None,
        draft: bool | None = None,
        resolved: bool = False,
    ) -> None:
        extra = extra_instructions or ""
        if not resolved:
            if context.chat_type == "private":
                project = await self.telegram_projects.active(context.user_id)
                conversation_id = project.openai_conversation_id
                extra = project.instructions
            else:
                conversation_id = await self.database.conversation_id(
                    context.chat_id, context.thread_id
                )
        markup = await self._private_reply_keyboard(context)
        if not conversation_id or not await self.conversations.has_items(conversation_id):
            await self.rich.send(message, "Nothing to catch up on yet.", reply_markup=markup)
            return
        await self._stream_turn(
            message,
            context,
            CATCHUP_PROMPT,
            conversation_id=conversation_id,
            extra_instructions=extra,
            draft=draft,
        )

    async def reset(self, message: Message) -> None:
        context = self._context(message)
        if context is None or not await self._require_access(message, context):
            return
        if context.chat_type == "private":
            project = await self.telegram_projects.active(context.user_id)
            await self.telegram_projects.reset(context.user_id, project.id)
            await self.rich.send(
                message,
                "Conversation reset. Long-term memory was not changed.",
                reply_markup=project_reply_keyboard(project),
            )
            return
        await self.conversations.reset(context.chat_id, context.thread_id)
        await self.rich.send(message, "Conversation reset. Long-term memory was not changed.")

    async def stop(self, message: Message) -> None:
        context = self._context(message)
        if context is None or not await self._require_access(message, context):
            return
        stopped = self.runtime.stop(context.chat_id, context.thread_id)
        await self.rich.send(message, "Stopping…" if stopped else "Nothing is running here.")

    async def admin(self, message: Message, state: FSMContext) -> None:
        context = self._context(message)
        if context is None or not self.access.is_owner(context.user_id):
            await self.rich.send(message, "This command is only available to the bot owner.")
            return
        await state.clear()
        await self._show_admin(
            message, context, reply_user=self._admin_reply_user(message), edit=False
        )

    async def admin_callback(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message or not isinstance(callback.message, Message) or not callback.data:
            await callback.answer()
            return
        context = self._context(callback.message, callback.from_user)
        if context is None or not self.access.is_owner(context.user_id):
            await callback.answer("This is only available to the bot owner.", show_alert=True)
            return
        action = callback.data.split(":")
        try:
            if action == ["admin", "home"]:
                await state.clear()
                await self._show_admin(callback.message, context)
            elif action == ["admin", "cancel"]:
                await state.clear()
                await self._show_admin(callback.message, context, notice="Cancelled.")
            elif action == ["admin", "allow_group"]:
                if context.chat_type not in {"group", "supergroup"}:
                    raise ValueError("Allow this group only works inside a group.")
                notice = await self._apply_admin(
                    context.user_id, "allow", Scope("chat", context.chat_id)
                )
                await self._show_admin(callback.message, context, notice=notice)
            elif action == ["admin", "remove_group"]:
                if context.chat_type not in {"group", "supergroup"}:
                    raise ValueError("Remove this group only works inside a group.")
                notice = await self._apply_admin(
                    context.user_id, "remove", Scope("chat", context.chat_id)
                )
                await self._show_admin(callback.message, context, notice=notice)
            elif len(action) == 3 and action[:2] == ["admin", "ask"]:
                if context.chat_type != "private":
                    raise PermissionError("Manage the full allowlist in a private chat.")
                prompt_action = action[2]
                if prompt_action not in {"allow", "ban", "remove"}:
                    raise ValueError("Unknown admin action.")
                await state.set_state(AdminPrompt.target)
                await state.set_data(
                    {
                        "action": prompt_action,
                        "prompt_message_id": callback.message.message_id,
                    }
                )
                await self.rich.edit(
                    callback.message,
                    RichMessages.admin_prompt(prompt_action),
                    reply_markup=self._admin_cancel_keyboard(),
                )
            elif len(action) == 4 and action[:2] == ["admin", "open"]:
                if context.chat_type != "private":
                    raise PermissionError("Manage the full allowlist in a private chat.")
                target = self._admin_scope(action[2], action[3])
                await self._show_admin_entry(callback.message, context, target)
            elif len(action) == 5 and action[:2] == ["admin", "set"]:
                target = self._admin_scope(action[3], action[4])
                if context.chat_type != "private" and target.kind != "user":
                    raise PermissionError("Manage the full allowlist in a private chat.")
                notice = await self._apply_admin(
                    context.user_id, self._admin_effect(action[2]), target
                )
                await self._show_admin(callback.message, context, notice=notice)
            elif len(action) == 4 and action[:2] == ["admin", "rm"]:
                if context.chat_type != "private":
                    raise PermissionError("Manage the full allowlist in a private chat.")
                target = self._admin_scope(action[2], action[3])
                notice = await self._apply_admin(context.user_id, "remove", target)
                await self._show_admin(callback.message, context, notice=notice)
            else:
                raise ValueError("Unknown admin action.")
        except (PermissionError, ValueError) as error:
            await callback.answer(str(error), show_alert=True)
            return
        await callback.answer()

    async def admin_prompt(self, message: Message, state: FSMContext) -> Any:
        context = self._context(message)
        if context is None or not self.access.is_owner(context.user_id):
            await state.clear()
            return None
        data = await state.get_data()
        action = data.get("action")
        prompt = self._admin_prompt_reply(message, data.get("prompt_message_id"))
        if prompt is None:
            return UNHANDLED
        if action not in {"allow", "ban", "remove"}:
            await state.clear()
            await self.rich.send(message, "That admin action is no longer active.")
            return None
        target = self._admin_scope_from_text(message.text)
        if target is None:
            await self.rich.send(
                message,
                "Reply to this message with a numeric Telegram id. Negative ids are groups.",
            )
            return None
        await state.clear()
        try:
            notice = await self._apply_admin(context.user_id, cast(AdminAction, action), target)
        except (PermissionError, ValueError) as error:
            await self.rich.send(message, str(error))
            return None
        await self._show_admin(prompt, context, notice=notice)
        return None

    async def _show_admin(
        self,
        message: Message,
        context: RequestContext,
        *,
        notice: RichTextUnion | None = None,
        reply_user: User | None = None,
        edit: bool = True,
    ) -> None:
        entries = await self.database.list_access()
        group_effect: AccessEffect | None = None
        in_group = context.chat_type in {"group", "supergroup"}
        if in_group:
            group_scope = Scope("chat", context.chat_id)
            group_effect = next(
                (entry.effect for entry in entries if entry.scope == group_scope),
                None,
            )
        visible = () if in_group else entries
        content = RichMessages.access(
            visible,
            notice=notice,
            group_effect=group_effect,
            in_group=in_group,
            show_entries=not in_group,
        )
        markup = self._admin_home_keyboard(context, visible, reply_user, group_effect)
        if edit:
            await self.rich.edit(message, content, reply_markup=markup)
        else:
            await self.rich.send(message, content, reply_markup=markup)

    async def _show_admin_entry(
        self, message: Message, context: RequestContext, target: Scope
    ) -> None:
        entries = await self.database.list_access()
        entry = next((item for item in entries if item.scope == target), None)
        if entry is None:
            await self._show_admin(message, context, notice="No matching entry.")
            return
        await self.rich.edit(
            message,
            RichMessages.access([entry], notice=RichMessages.access_target(entry.scope)),
            reply_markup=self._admin_entry_keyboard(entry),
        )

    async def _apply_admin(
        self, actor_id: int, action: AdminAction, target: Scope
    ) -> RichTextUnion:
        if (
            target.kind == "user"
            and self.access.is_owner(target.id)
            and action in {"ban", "remove"}
        ):
            raise PermissionError("The owner cannot be banned or removed.")
        if action == "allow" or action == "ban":
            await self.database.set_access(target, action, actor_id)
            return RichMessages.access_change(f"{action.title()}ed", target)
        if action == "remove":
            removed = await self.database.remove_access(target)
            return (
                RichMessages.access_change("Removed", target) if removed else "No matching entry."
            )
        raise ValueError("Unknown admin action.")

    async def chat(self, message: Message) -> None:
        context = self._context(message)
        if context is None:
            return
        if context.chat_type != "private" and not await self._directed_at_bot(message):
            return
        if message.successful_payment or message.refunded_payment:
            return
        if not await self._require_access(message, context):
            return
        media_groups = getattr(self, "media_groups", None)
        album: tuple[MediaGroupItem, ...] = ()
        if media_groups is not None:
            album = await media_groups.resolve(message)
            if message.media_group_id is not None and not await media_groups.claim(message):
                return
        input_file_ids: list[str] = []
        try:
            user_input = await self._input(message, context, album=album, file_ids=input_file_ids)
        except ValueError as error:
            await self.rich.send(message, str(error))
            return
        conversation_id: str | None = None
        extra_instructions = ""
        if context.chat_type == "private":
            project = await self.telegram_projects.active(context.user_id)
            conversation_id = await self.telegram_projects.conversation_id(project)
            extra_instructions = project.instructions
        await self._stream_turn(
            message,
            context,
            user_input,
            conversation_id=conversation_id,
            extra_instructions=extra_instructions,
            input_file_ids=tuple(input_file_ids),
        )

    async def _stream_turn(
        self,
        message: Message,
        context: RequestContext,
        user_input: str | list[TResponseInputItem],
        *,
        conversation_id: str | None = None,
        extra_instructions: str = "",
        draft: bool | None = None,
        input_file_ids: tuple[str, ...] = (),
    ) -> None:
        current = await self.database.get_settings(context.scope)
        current = await self.billing.clamp_settings(context, current, self.access)
        billed_user_id = await self.access.billed_user_id(context)
        try:
            await self.quota.check(context, billed_user_id=billed_user_id)
        except AllowanceError as error:
            await self.rich.send(message, error.message)
            return
        placeholder: Message | None = None
        use_draft = context.chat_type == "private" if draft is None else draft
        if use_draft:
            await self.rich.draft(message)
        else:
            placeholder = await self.rich.send(message, "Thinking…")
        last_edit = 0.0
        streamed_text = ""

        async def on_text(text: str) -> None:
            nonlocal last_edit, streamed_text
            streamed_text = text
            now = time.monotonic()
            if now - last_edit < 0.8:
                return
            last_edit = now
            with suppress(TelegramBadRequest):
                if use_draft:
                    await self.rich.draft(message, text[:32000])
                elif placeholder:
                    await self.rich.edit(placeholder, text[:32000] or "Thinking…")

        try:
            output = await self.runtime.run(
                context,
                current,
                user_input,
                on_text,
                input_file_ids=input_file_ids,
                conversation_id=conversation_id,
                extra_instructions=extra_instructions,
            )
            await self.quota.record(context, output.usage_tokens, billed_user_id=billed_user_id)
            if context.chat_type != "private":
                await self.groups.mark_seen(message)
            await self._deliver(message, placeholder, output)
        except AllowanceError as error:
            await self._finish(message, placeholder, error.message)
        except TimeoutError:
            await self._finish(message, placeholder, "This took too long, so I stopped it.")
        except asyncio.CancelledError:
            await self._finish(message, placeholder, "Stopped.")
        except ContextLimitError as error:
            await self._finish(message, placeholder, str(error))
        except StreamStartedError:
            content = streamed_text.strip()
            if content:
                content += "\n\n_Response interrupted. Please continue with a new message._"
                await self._finish(message, placeholder, self.rich.output(content))
            else:
                await self._finish(message, placeholder, "The response was interrupted. Try again.")
        except Exception as error:
            log.exception(
                "agent_run_failed",
                chat_id=context.chat_id,
                thread_id=context.thread_id,
                error=type(error).__name__,
                error_detail=str(error)[:300],
            )
            await self._finish(message, placeholder, "Something went wrong. Please try again.")

    async def _input(
        self,
        message: Message,
        context: RequestContext,
        *,
        album: Sequence[MediaGroupItem] | None = None,
        file_ids: list[str] | None = None,
    ) -> str | list[TResponseInputItem]:
        if album is None:
            media_groups = getattr(self, "media_groups", None)
            album = await media_groups.resolve(message) if media_groups is not None else ()
        text = self.groups.text(message)
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
                excerpt = self.groups.text(reply)
                reply_context = (
                    f"\nReplying to {reply_identity} #{reply.message_id}: {excerpt[:500]}"
                )
            history = await self.groups.history(message)
            if history.transcript:
                text = (
                    '<recent_group_context format="json" trust="untrusted">\n'
                    f"{history.transcript}\n</recent_group_context>\n\n"
                    f"<current_message>\n{identity}: {text}{reply_context}\n</current_message>"
                )
            else:
                text = f"<current_message>\n{identity}: {text}{reply_context}\n</current_message>"
        has_attachments = any(
            (
                source
                and (
                    source.photo
                    or source.voice
                    or source.audio
                    or source.video_note
                    or source.video
                    or source.document
                )
            )
            for source in (message, message.reply_to_message)
        )
        if not has_attachments and not album:
            return text

        content: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
        attached_file_ids = await self.attachments.add(message, content, album=album)
        if file_ids is not None:
            file_ids.extend(attached_file_ids)
        return cast(list[TResponseInputItem], [{"role": "user", "content": content}])

    async def _deliver(
        self, target: Message, placeholder: Message | None, output: RunOutput
    ) -> None:
        chunks = self._chunks(output.text)
        chunks = chunks or [""]
        first = self.rich.output(chunks[0])
        await self._finish(target, placeholder, first)
        for chunk in chunks[1:]:
            await self.rich.send(target, self.rich.output(chunk))
        if output.images:
            await self.rich.send_images(target, output.images)
        if output.files:
            await self.rich.send_documents(target, output.files)

    async def _finish(
        self, target: Message, placeholder: Message | None, content: str | InputRichMessage
    ) -> None:
        if placeholder:
            await self.rich.edit(placeholder, content)
            return
        markup = None
        projects = getattr(self, "telegram_projects", None)
        if target.chat.type == "private" and projects is not None:
            context = self._context(target)
            if context is not None:
                markup = await self._private_reply_keyboard(context)
        await self.rich.send(target, content, reply_markup=markup)

    async def _private_reply_keyboard(self, context: RequestContext) -> ReplyKeyboardMarkup | None:
        if context.chat_type != "private":
            return None
        project = await self.telegram_projects.active(context.user_id)
        return project_reply_keyboard(project)

    async def _require_access(self, message: Message, context: RequestContext) -> bool:
        if await self.access.allowed(context):
            return True
        await self._deny_access(message, context)
        return False

    async def _deny_access(self, message: Message, context: RequestContext) -> None:
        if context.chat_type == "private":
            await self.rich.send(message, "This account is banned.")
            return
        if await self.database.access_effect(Scope("user", context.user_id)) == "ban":
            await self.rich.send(message, "This account is banned.")
            return
        await self.rich.send(message, GROUP_NEEDS_PLUS)

    async def _require_plus(self, message: Message, context: RequestContext) -> bool:
        if await self.access.plus(context):
            return True
        await self.rich.send(message, self.rich.plus_agents())
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
            thread_id=thread_id(message),
            username=sender.username,
            display_name=name or sender.username or "User",
        )

    @staticmethod
    def _admin_reply_user(message: Message) -> User | None:
        reply = message.reply_to_message
        if reply and reply.from_user and not reply.from_user.is_bot:
            return reply.from_user
        return None

    @staticmethod
    def _admin_prompt_reply(message: Message, prompt_message_id: object) -> Message | None:
        reply = message.reply_to_message
        if reply is None or reply.message_id != prompt_message_id:
            return None
        return reply

    @staticmethod
    def _admin_scope_from_text(raw_id: str | None) -> Scope | None:
        if raw_id is None:
            return None
        try:
            telegram_id = int(raw_id.strip())
        except ValueError:
            return None
        return Scope("chat" if telegram_id < 0 else "user", telegram_id)

    @staticmethod
    def _admin_effect(raw: str) -> AccessEffect:
        if raw == "allow":
            return "allow"
        if raw == "ban":
            return "ban"
        raise ValueError("Unknown admin action.")

    @staticmethod
    def _admin_scope(kind: str, raw_id: str) -> Scope:
        if kind not in {"user", "chat"}:
            raise ValueError("Unknown admin target.")
        try:
            telegram_id = int(raw_id)
        except ValueError as error:
            raise ValueError("Unknown admin target.") from error
        expected: ScopeKind = "chat" if telegram_id < 0 else "user"
        if kind != expected:
            raise ValueError("Unknown admin target.")
        return Scope(expected, telegram_id)

    @staticmethod
    def _admin_home_keyboard(
        context: RequestContext,
        entries: Sequence[AccessEntry],
        reply_user: User | None,
        group_effect: AccessEffect | None,
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        if context.chat_type in {"group", "supergroup"}:
            group_row: list[InlineKeyboardButton] = []
            if group_effect is not None:
                group_row.append(
                    InlineKeyboardButton(
                        text="Remove this group", callback_data="admin:remove_group"
                    )
                )
            if group_effect != "allow":
                group_row.append(
                    InlineKeyboardButton(text="Allow this group", callback_data="admin:allow_group")
                )
            if group_row:
                rows.append(group_row)
        if reply_user is not None:
            name = (reply_user.first_name or "user")[:20]
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Allow {name}",
                        callback_data=f"admin:set:allow:user:{reply_user.id}",
                    ),
                    InlineKeyboardButton(
                        text=f"Ban {name}",
                        callback_data=f"admin:set:ban:user:{reply_user.id}",
                    ),
                ]
            )
        if context.chat_type == "private":
            rows.append(
                [
                    InlineKeyboardButton(text="Allow", callback_data="admin:ask:allow"),
                    InlineKeyboardButton(text="Ban", callback_data="admin:ask:ban"),
                ]
            )
            rows.append([InlineKeyboardButton(text="Remove", callback_data="admin:ask:remove")])
            rows.extend(
                [
                    [
                        InlineKeyboardButton(
                            text=f"{entry.scope.kind} {entry.scope.id} · {entry.effect}",
                            callback_data=f"admin:open:{entry.scope.kind}:{entry.scope.id}",
                        )
                    ]
                    for entry in entries
                ]
            )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def _admin_entry_keyboard(entry: AccessEntry) -> InlineKeyboardMarkup:
        kind = entry.scope.kind
        telegram_id = entry.scope.id
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Allow", callback_data=f"admin:set:allow:{kind}:{telegram_id}"
                    ),
                    InlineKeyboardButton(
                        text="Ban", callback_data=f"admin:set:ban:{kind}:{telegram_id}"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Remove", callback_data=f"admin:rm:{kind}:{telegram_id}"
                    )
                ],
                [InlineKeyboardButton(text="‹ Back", callback_data="admin:home")],
            ]
        )

    @staticmethod
    def _admin_cancel_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="admin:cancel")]]
        )

    @staticmethod
    def _help_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Website", url=WEBSITE_URL),
                    InlineKeyboardButton(text="Docs", url=DOCS_URL),
                ],
                [InlineKeyboardButton(text="Privacy policy", url=PRIVACY_URL)],
            ]
        )

    @staticmethod
    def _settings_keyboard(editable: bool, *, private: bool = False) -> InlineKeyboardMarkup | None:
        if not editable:
            rows = []
            if private:
                rows.append(
                    [InlineKeyboardButton(text="Projects", callback_data="settings:projects")]
                )
            rows.extend(
                [
                    [InlineKeyboardButton(text="Connectors", callback_data="settings:connectors")],
                    [InlineKeyboardButton(text="Skills", callback_data="settings:skills")],
                ]
            )
            return InlineKeyboardMarkup(inline_keyboard=rows)
        rows = []
        if private:
            rows.append([InlineKeyboardButton(text="Projects", callback_data="settings:projects")])
        rows.extend(
            [
                [
                    InlineKeyboardButton(text="Reasoning", callback_data="settings:reasoning"),
                    InlineKeyboardButton(text="Agent", callback_data="settings:agents"),
                ],
                [
                    InlineKeyboardButton(text="Connectors", callback_data="settings:connectors"),
                    InlineKeyboardButton(text="Skills", callback_data="settings:skills"),
                ],
                [InlineKeyboardButton(text="Memory", callback_data="settings:memory")],
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def _agents_keyboard(
        agents: list[InstalledAgent], active_agent_id: str | None, editable: bool
    ) -> InlineKeyboardMarkup | None:
        rows = [
            [
                InlineKeyboardButton(
                    text=("✓ " if item.profile.id == active_agent_id else "") + item.version.name,
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
            rows.extend(
                [
                    [
                        InlineKeyboardButton(text="Edit", callback_data=f"agents:edit:{agent_id}"),
                        InlineKeyboardButton(
                            text="Share", callback_data=f"agents:share:{agent_id}"
                        ),
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
                    [InlineKeyboardButton(text="Delete all", callback_data="settings:memory:clear")]
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


def dump_update(update: Update) -> str:
    return update.model_dump_json(exclude_none=True, fallback=_update_fallback)


def _update_fallback(value: object) -> None:
    if isinstance(value, Default):
        return None
    raise TypeError(f"Unable to serialize {type(value)!r}")


WEBSITE_URL = "https://skye-bot.com"
DOCS_URL = "https://ai.skye-bot.com/"
PRIVACY_URL = "https://ai.skye-bot.com/privacy"

COMMANDS = [
    BotCommand(command="start", description="Start Skye"),
    BotCommand(command="help", description="Show capabilities"),
    BotCommand(command="account", description="Skye plans and Telegram Stars"),
    BotCommand(command="settings", description="Model, agent, memory, connectors, and skills"),
    BotCommand(command="agents", description="Create and manage agents"),
    BotCommand(command="reset", description="Start a new conversation"),
    BotCommand(command="stop", description="Stop the active task"),
    BotCommand(command="admin", description="Manage access (owner)"),
]

PRIVATE_COMMANDS = [
    BotCommand(command="start", description="Start Skye"),
    BotCommand(command="help", description="Show capabilities"),
    BotCommand(command="account", description="Skye plans and Telegram Stars"),
    BotCommand(command="settings", description="Model, agent, memory, connectors, and skills"),
    BotCommand(command="projects", description="Switch and manage project chats"),
    BotCommand(command="agents", description="Create and manage agents"),
    BotCommand(command="catchup", description="Summarize this conversation"),
    BotCommand(command="reset", description="Start a new conversation"),
    BotCommand(command="stop", description="Stop the active task"),
    BotCommand(command="admin", description="Manage access (owner)"),
]
