from __future__ import annotations

import asyncio
import re
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence

import structlog
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from openai import AsyncOpenAI

from .db import Database
from .models import RequestContext, TelegramProject
from .rich import RichMessages

log = structlog.get_logger()

DEFAULT_EMOJI = "☁️"
PRESET_EMOJIS: tuple[str, ...] = (
    "☁️",
    "💬",
    "💻",
    "⚙️",
    "💼",
    "🎓",
    "❤️",
    "✨",
    "🌍",
    "🎨",
    "🧪",
    "🎵",
    "📷",
    "📁",
    "💡",
    "⭐",
)
MAX_PROJECTS = 50
PAGE_SIZE = 8
MAX_EMOJI_CHARS = 16
_LEADING_EMOJI = re.compile(
    r"^("
    r"[\U0001F1E6-\U0001F1FF]{2}"
    r"|[#*0-9]\uFE0F?\u20E3"
    r"|[\u00A9\u00AE\u203C\u2049\u2122\u2139\u2194-\u21AA\u231A\u2328\u23CF"
    r"\u23E9-\u23FA\u24C2\u25AA-\u25FE\u2600-\u27BF\u2934\u2935\u2B05-\u2B07"
    r"\u2B1B\u2B1C\u2B50\u2B55\u3030\u303D\u3297\u3299"
    r"\U0001F000-\U0001F02F\U0001F0A0-\U0001F0FF\U0001F100-\U0001F1FF"
    r"\U0001F300-\U0001FAFF\U0001F200-\U0001F2FF]"
    r"[\uFE0E\uFE0F]?"
    r"[\U0001F3FB-\U0001F3FF]?"
    r"(?:\u200D[\u2600-\u27BF\U0001F300-\U0001FAFF][\uFE0E\uFE0F]?"
    r"[\U0001F3FB-\U0001F3FF]?)*"
    r")"
)


class ProjectWizard(StatesGroup):
    name = State()
    emoji = State()
    instructions = State()
    edit = State()


class TelegramProjectError(ValueError):
    """User-facing project failure."""


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def parse_leading_emoji(text: str) -> tuple[str | None, str]:
    cleaned = " ".join(text.split())
    if not cleaned:
        return None, ""
    for emoji in sorted(PRESET_EMOJIS, key=len, reverse=True):
        if cleaned.startswith(emoji):
            return emoji, cleaned[len(emoji) :].strip()
    match = _LEADING_EMOJI.match(cleaned)
    if match:
        return match.group(1), cleaned[match.end() :].strip()
    return None, cleaned


def parse_standalone_emoji(text: str) -> str | None:
    emoji, rest = parse_leading_emoji(text)
    if emoji is None or rest or len(emoji) > MAX_EMOJI_CHARS:
        return None
    return emoji


def parse_project_callback(data: str) -> tuple[str, ...] | None:
    if not data.startswith("proj:"):
        return None
    parts = tuple(data.split(":")[1:])
    return parts or None


def from_settings_origin(action: Sequence[str], index: int) -> bool:
    return len(action) > index and action[index] == "s"


def origin_suffix(from_settings: bool) -> str:
    return ":s" if from_settings else ""


class TelegramProjectService:
    def __init__(self, database: Database, client: AsyncOpenAI) -> None:
        self.database = database
        self.client = client
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def ensure_skye(self, user_id: int) -> TelegramProject:
        existing = await self.database.skye_telegram_project(user_id)
        if existing is not None:
            await self._ensure_active(user_id, existing)
            return existing
        migrated = await self.database.conversation_id(user_id, 0)
        created = await self.database.create_telegram_project(
            TelegramProject(
                id=new_id(),
                user_id=user_id,
                kind="skye",
                name="Skye",
                emoji=DEFAULT_EMOJI,
                instructions="",
                openai_conversation_id=migrated,
                created_at="",
                updated_at="",
            )
        )
        if migrated:
            await self.database.pop_conversation(user_id, 0)
        await self.database.set_active_telegram_project_id(user_id, created.id)
        return created

    async def list(self, user_id: int) -> list[TelegramProject]:
        await self.ensure_skye(user_id)
        return await self.database.list_telegram_projects(user_id)

    async def active(self, user_id: int) -> TelegramProject:
        await self.ensure_skye(user_id)
        project_id = await self.database.active_telegram_project_id(user_id)
        if project_id:
            project = await self.database.telegram_project(user_id, project_id)
            if project is not None:
                return project
        skye = await self.database.skye_telegram_project(user_id)
        if skye is None:
            raise RuntimeError("Skye project was not created")
        await self.database.set_active_telegram_project_id(user_id, skye.id)
        return skye

    async def require(self, user_id: int, project_id: str) -> TelegramProject:
        project = await self.database.telegram_project(user_id, project_id)
        if project is None:
            raise LookupError("Project not found.")
        return project

    async def create(
        self,
        user_id: int,
        *,
        name: str,
        emoji: str = DEFAULT_EMOJI,
        instructions: str = "",
    ) -> TelegramProject:
        await self.ensure_skye(user_id)
        current = await self.database.list_telegram_projects(user_id)
        if len(current) >= MAX_PROJECTS:
            raise TelegramProjectError(f"You can keep up to {MAX_PROJECTS} projects.")
        created = await self.database.create_telegram_project(
            TelegramProject(
                id=new_id(),
                user_id=user_id,
                kind="custom",
                name=self._name(name),
                emoji=self._emoji(emoji),
                instructions=self._instructions(instructions),
                openai_conversation_id=None,
                created_at="",
                updated_at="",
            )
        )
        await self.database.set_active_telegram_project_id(user_id, created.id)
        return created

    async def update(
        self,
        user_id: int,
        project_id: str,
        *,
        name: str | None = None,
        emoji: str | None = None,
        instructions: str | None = None,
    ) -> TelegramProject:
        project = await self.require(user_id, project_id)
        if project.kind == "skye" and name is not None and name.strip() != "Skye":
            name = "Skye"
        updated = await self.database.update_telegram_project(
            user_id,
            project_id,
            name=None if name is None else self._name(name),
            emoji=None if emoji is None else self._emoji(emoji),
            instructions=None if instructions is None else self._instructions(instructions),
        )
        if updated is None:
            raise LookupError("Project not found.")
        return updated

    async def select(self, user_id: int, project_id: str) -> TelegramProject:
        project = await self.require(user_id, project_id)
        await self.database.set_active_telegram_project_id(user_id, project.id)
        await self.database.touch_telegram_project(user_id, project.id)
        return project

    async def delete(self, user_id: int, project_id: str) -> TelegramProject:
        project = await self.database.delete_telegram_project(user_id, project_id)
        if project is None:
            raise LookupError("Project not found.")
        await self._delete_conversation(project.openai_conversation_id)
        active_id = await self.database.active_telegram_project_id(user_id)
        if active_id == project.id:
            skye = await self.ensure_skye(user_id)
            await self.database.set_active_telegram_project_id(user_id, skye.id)
        return project

    async def reset(self, user_id: int, project_id: str) -> TelegramProject:
        project = await self.require(user_id, project_id)
        await self._delete_conversation(project.openai_conversation_id)
        await self.database.set_telegram_conversation(user_id, project_id, None)
        return await self.require(user_id, project_id)

    async def conversation_id(self, project: TelegramProject) -> str:
        async with self._locks[project.id]:
            current = await self.require(project.user_id, project.id)
            if current.openai_conversation_id:
                return current.openai_conversation_id
            conversation = await self.client.conversations.create(
                metadata={
                    "telegram_user": str(project.user_id),
                    "telegram_project": project.id,
                }
            )
            await self.database.set_telegram_conversation(
                project.user_id, project.id, conversation.id
            )
            return conversation.id

    async def _ensure_active(self, user_id: int, project: TelegramProject) -> None:
        active_id = await self.database.active_telegram_project_id(user_id)
        if active_id:
            existing = await self.database.telegram_project(user_id, active_id)
            if existing is not None:
                return
        await self.database.set_active_telegram_project_id(user_id, project.id)

    async def _delete_conversation(self, conversation_id: str | None) -> None:
        if not conversation_id:
            return
        try:
            await self.client.conversations.delete(conversation_id)
        except Exception as error:
            log.warning(
                "telegram_project_conversation_delete_failed",
                conversation_id=conversation_id,
                error=type(error).__name__,
            )

    @staticmethod
    def _name(name: str) -> str:
        cleaned = " ".join(name.split())
        if not 1 <= len(cleaned) <= 64:
            raise TelegramProjectError("Project name must be 1–64 characters.")
        return cleaned

    @staticmethod
    def _instructions(instructions: str) -> str:
        text = instructions.strip()
        if len(text) > 12_000:
            raise TelegramProjectError("Instructions must be at most 12,000 characters.")
        return text

    @staticmethod
    def _emoji(emoji: str) -> str:
        parsed = parse_standalone_emoji(emoji)
        if parsed is None:
            raise TelegramProjectError("Send a single emoji.")
        return parsed


def project_reply_keyboard(project: TelegramProject) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/projects"), KeyboardButton(text="/catchup")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=project.label,
    )


def projects_keyboard(
    projects: Sequence[TelegramProject],
    active_id: str,
    *,
    page: int = 0,
    from_settings: bool = False,
) -> InlineKeyboardMarkup:
    suffix = origin_suffix(from_settings)
    start = page * PAGE_SIZE
    visible = projects[start : start + PAGE_SIZE]
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=("✓ " if item.id == active_id else "") + f"{item.emoji} {item.name}",
                callback_data=f"proj:open:{item.id}{suffix}",
            )
        ]
        for item in visible
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="‹ Prev", callback_data=f"proj:page:{page - 1}{suffix}")
        )
    if start + PAGE_SIZE < len(projects):
        nav.append(
            InlineKeyboardButton(text="Next ›", callback_data=f"proj:page:{page + 1}{suffix}")
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="New project", callback_data=f"proj:new{suffix}")])
    if from_settings:
        rows.append([InlineKeyboardButton(text="‹ Back", callback_data="settings:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def project_keyboard(
    project: TelegramProject,
    *,
    active: bool,
    confirm: str | None = None,
    from_settings: bool = False,
) -> InlineKeyboardMarkup:
    suffix = origin_suffix(from_settings)
    if confirm == "delete":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Delete", callback_data=f"proj:yes:{project.id}"),
                    InlineKeyboardButton(
                        text="Cancel", callback_data=f"proj:open:{project.id}{suffix}"
                    ),
                ]
            ]
        )
    if confirm == "reset":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Reset", callback_data=f"proj:wipe:{project.id}"),
                    InlineKeyboardButton(
                        text="Cancel", callback_data=f"proj:open:{project.id}{suffix}"
                    ),
                ]
            ]
        )
    rows: list[list[InlineKeyboardButton]] = []
    if not active:
        rows.append(
            [InlineKeyboardButton(text="Switch", callback_data=f"proj:use:{project.id}{suffix}")]
        )
    else:
        rows.append(
            [InlineKeyboardButton(text="Catch up", callback_data=f"proj:catch:{project.id}")]
        )
    rows.append(
        [
            InlineKeyboardButton(text="Rename", callback_data=f"proj:name:{project.id}"),
            InlineKeyboardButton(text="Emoji", callback_data=f"proj:emoji:{project.id}"),
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="Instructions", callback_data=f"proj:inst:{project.id}")]
    )
    rows.append(
        [InlineKeyboardButton(text="Reset chat", callback_data=f"proj:reset:{project.id}{suffix}")]
    )
    if project.deletable:
        rows.append(
            [InlineKeyboardButton(text="Delete", callback_data=f"proj:del:{project.id}{suffix}")]
        )
    rows.append(
        [InlineKeyboardButton(text="‹ Back", callback_data=f"proj:home{suffix}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def emoji_keyboard(
    *,
    project_id: str | None = None,
    from_settings: bool = False,
) -> InlineKeyboardMarkup:
    suffix = origin_suffix(from_settings)
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for index, emoji in enumerate(PRESET_EMOJIS):
        callback = (
            f"proj:emo:{index}" if project_id is None else f"proj:icon:{project_id}:{index}"
        )
        row.append(InlineKeyboardButton(text=emoji, callback_data=callback))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if project_id is None:
        rows.append([InlineKeyboardButton(text="Send any emoji", callback_data="proj:any")])
        rows.append([InlineKeyboardButton(text="Cancel", callback_data=f"proj:home{suffix}")])
    else:
        rows.append(
            [InlineKeyboardButton(text="Send any emoji", callback_data=f"proj:any:{project_id}")]
        )
        rows.append(
            [InlineKeyboardButton(text="Cancel", callback_data=f"proj:open:{project_id}{suffix}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def skip_keyboard(*, from_settings: bool = False) -> InlineKeyboardMarkup:
    suffix = origin_suffix(from_settings)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Skip", callback_data="proj:skip")],
            [InlineKeyboardButton(text="Cancel", callback_data=f"proj:home{suffix}")],
        ]
    )


def cancel_keyboard(
    *, from_settings: bool = False, project_id: str | None = None
) -> InlineKeyboardMarkup:
    suffix = origin_suffix(from_settings)
    back = f"proj:home{suffix}" if project_id is None else f"proj:open:{project_id}{suffix}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data=back)]]
    )


class ProjectPanel:
    def __init__(
        self,
        service: TelegramProjectService,
        rich: RichMessages,
        bot: Bot,
    ) -> None:
        self.service = service
        self.rich = rich
        self.bot = bot

    async def show_home(
        self,
        message: Message,
        context: RequestContext,
        *,
        edit: bool = True,
        page: int = 0,
        from_settings: bool = False,
        notice: str | None = None,
    ) -> None:
        projects = await self.service.list(context.user_id)
        active = await self.service.active(context.user_id)
        content = self.rich.projects(projects, active.id, notice=notice)
        markup = projects_keyboard(
            projects, active.id, page=page, from_settings=from_settings
        )
        if edit:
            await self.rich.edit(message, content, reply_markup=markup)
        else:
            await self.rich.send(message, content, reply_markup=markup)

    async def handle_callback(
        self,
        message: Message,
        context: RequestContext,
        action: Sequence[str],
        state: FSMContext,
        *,
        catchup: Callable[[Message, RequestContext, str], Awaitable[None]] | None = None,
    ) -> None:
        if action == ["home"] or action == ["home", "s"]:
            await state.clear()
            await self.show_home(
                message, context, from_settings=from_settings_origin(action, 1)
            )
            return
        if action and action[0] == "page":
            page = int(action[1]) if len(action) > 1 and action[1].isdigit() else 0
            await self.show_home(
                message,
                context,
                page=page,
                from_settings=from_settings_origin(action, 2),
            )
            return
        if action == ["new"] or action == ["new", "s"]:
            await state.set_state(ProjectWizard.name)
            await state.set_data(
                {
                    "user_id": context.user_id,
                    "from_settings": action[-1] == "s" if action else False,
                    "panel_message_id": message.message_id,
                }
            )
            await self.rich.edit(
                message,
                self.rich.project_name_prompt(),
                reply_markup=cancel_keyboard(from_settings=action[-1:] == ["s"]),
            )
            return
        if action == ["any"] or (len(action) == 2 and action[0] == "any"):
            project_id = action[1] if len(action) == 2 else None
            data = await state.get_data()
            from_settings = bool(data.get("from_settings"))
            if project_id is None:
                await state.set_state(ProjectWizard.emoji)
            else:
                await state.set_state(ProjectWizard.edit)
                await state.update_data(field="emoji", project_id=project_id)
            await self.rich.edit(
                message,
                self.rich.project_emoji_prompt(),
                reply_markup=cancel_keyboard(
                    from_settings=from_settings, project_id=project_id
                ),
            )
            return
        if len(action) == 2 and action[0] == "emo" and action[1].isdigit():
            await self._apply_create_emoji(message, state, int(action[1]))
            return
        if action == ["skip"]:
            await self._finish_create(message, context, state, instructions="")
            return
        if len(action) >= 2 and action[0] == "open":
            await state.clear()
            project = await self.service.require(context.user_id, action[1])
            await self._show_project(
                message,
                context,
                project,
                from_settings=from_settings_origin(action, 2),
            )
            return
        if len(action) >= 2 and action[0] == "use":
            project = await self.service.select(context.user_id, action[1])
            await self._show_project(
                message,
                context,
                project,
                from_settings=from_settings_origin(action, 2),
            )
            await self.rich.send(
                message,
                f"Switched to {project.label}.",
                reply_markup=project_reply_keyboard(project),
            )
            return
        if len(action) == 2 and action[0] == "catch":
            if catchup is not None:
                await catchup(message, context, action[1])
            return
        if len(action) == 2 and action[0] == "name":
            await self._start_edit(message, context, state, action[1], "name")
            return
        if len(action) == 2 and action[0] == "emoji":
            await state.set_state(ProjectWizard.edit)
            await state.set_data(
                {
                    "user_id": context.user_id,
                    "project_id": action[1],
                    "field": "emoji",
                    "from_settings": False,
                    "panel_message_id": message.message_id,
                }
            )
            await self.rich.edit(
                message,
                self.rich.project_emoji_prompt(),
                reply_markup=emoji_keyboard(project_id=action[1]),
            )
            return
        if len(action) == 3 and action[0] == "icon" and action[2].isdigit():
            emoji = PRESET_EMOJIS[int(action[2])]
            project = await self.service.update(context.user_id, action[1], emoji=emoji)
            await state.clear()
            await self._show_project(message, context, project)
            return
        if len(action) == 2 and action[0] == "inst":
            await self._start_edit(message, context, state, action[1], "instructions")
            return
        if len(action) >= 2 and action[0] == "reset":
            project = await self.service.require(context.user_id, action[1])
            await self.rich.edit(
                message,
                self.rich.project_reset_confirm(project),
                reply_markup=project_keyboard(
                    project,
                    active=True,
                    confirm="reset",
                    from_settings=from_settings_origin(action, 2),
                ),
            )
            return
        if len(action) == 2 and action[0] == "wipe":
            project = await self.service.reset(context.user_id, action[1])
            await self._show_project(
                message, context, project, notice="Conversation reset. Memory was not changed."
            )
            return
        if len(action) >= 2 and action[0] == "del":
            project = await self.service.require(context.user_id, action[1])
            await self.rich.edit(
                message,
                self.rich.project_delete_confirm(project),
                reply_markup=project_keyboard(
                    project,
                    active=False,
                    confirm="delete",
                    from_settings=from_settings_origin(action, 2),
                ),
            )
            return
        if len(action) == 2 and action[0] == "yes":
            await self.service.delete(context.user_id, action[1])
            await self.show_home(message, context, notice="Project deleted.")
            return
        raise TelegramProjectError("Unknown project action.")

    async def handle_wizard(
        self, message: Message, context: RequestContext, state: FSMContext
    ) -> None:
        data = await state.get_data()
        if data.get("user_id") != context.user_id:
            await state.clear()
            raise TelegramProjectError("This project draft belongs to another chat.")
        current = await state.get_state()
        text = (message.text or "").strip()
        if not text:
            raise TelegramProjectError("Send text for this step.")
        if current == ProjectWizard.name.state:
            emoji, name = parse_leading_emoji(text)
            if not name:
                raise TelegramProjectError("Project name must be 1–64 characters.")
            self.service._name(name)
            await state.update_data(name=name, emoji=emoji)
            if emoji is None:
                await state.set_state(ProjectWizard.emoji)
                await self.rich.send(
                    message,
                    self.rich.project_emoji_prompt(),
                    reply_markup=emoji_keyboard(
                        from_settings=bool(data.get("from_settings"))
                    ),
                )
                return
            await state.set_state(ProjectWizard.instructions)
            await self.rich.send(
                message,
                self.rich.project_instructions_prompt(),
                reply_markup=skip_keyboard(from_settings=bool(data.get("from_settings"))),
            )
            return
        if current == ProjectWizard.emoji.state:
            parsed = parse_standalone_emoji(text)
            if parsed is None:
                raise TelegramProjectError("Send a single emoji.")
            await state.update_data(emoji=parsed)
            await state.set_state(ProjectWizard.instructions)
            await self.rich.send(
                message,
                self.rich.project_instructions_prompt(),
                reply_markup=skip_keyboard(from_settings=bool(data.get("from_settings"))),
            )
            return
        if current == ProjectWizard.instructions.state:
            await self._finish_create(message, context, state, instructions=text, reply=True)
            return
        if current == ProjectWizard.edit.state:
            await self._apply_edit(message, context, state, text)
            return
        raise TelegramProjectError("This project draft is no longer active.")

    async def _start_edit(
        self,
        message: Message,
        context: RequestContext,
        state: FSMContext,
        project_id: str,
        field: str,
    ) -> None:
        project = await self.service.require(context.user_id, project_id)
        await state.set_state(ProjectWizard.edit)
        await state.set_data(
            {
                "user_id": context.user_id,
                "project_id": project_id,
                "field": field,
                "from_settings": False,
                "panel_message_id": message.message_id,
            }
        )
        if field == "name":
            content = self.rich.project_name_prompt(project.name)
        else:
            content = self.rich.project_instructions_prompt(keep=True)
        await self.rich.edit(
            message,
            content,
            reply_markup=cancel_keyboard(project_id=project_id),
        )

    async def _apply_create_emoji(
        self,
        message: Message,
        state: FSMContext,
        index: int,
    ) -> None:
        if not 0 <= index < len(PRESET_EMOJIS):
            raise TelegramProjectError("Unknown emoji.")
        if await state.get_state() not in {ProjectWizard.name.state, ProjectWizard.emoji.state}:
            await state.set_state(ProjectWizard.emoji)
        await state.update_data(emoji=PRESET_EMOJIS[index])
        data = await state.get_data()
        if not data.get("name"):
            raise TelegramProjectError("Send a project name first.")
        await state.set_state(ProjectWizard.instructions)
        await self.rich.edit(
            message,
            self.rich.project_instructions_prompt(),
            reply_markup=skip_keyboard(from_settings=bool(data.get("from_settings"))),
        )

    async def _finish_create(
        self,
        message: Message,
        context: RequestContext,
        state: FSMContext,
        *,
        instructions: str,
        reply: bool = False,
    ) -> None:
        if await state.get_state() not in {
            ProjectWizard.instructions.state,
            ProjectWizard.emoji.state,
            ProjectWizard.name.state,
        }:
            raise TelegramProjectError("This project draft is no longer active.")
        data = await state.get_data()
        name = str(data.get("name") or "")
        emoji = str(data.get("emoji") or DEFAULT_EMOJI)
        project = await self.service.create(
            context.user_id, name=name, emoji=emoji, instructions=instructions
        )
        await state.clear()
        notice = f"Created {project.label}. This chat continues here."
        if not reply:
            await self._show_project(message, context, project)
        await self.rich.send(message, notice, reply_markup=project_reply_keyboard(project))

    async def _apply_edit(
        self,
        message: Message,
        context: RequestContext,
        state: FSMContext,
        text: str,
    ) -> None:
        data = await state.get_data()
        project_id = str(data.get("project_id") or "")
        field = str(data.get("field") or "")
        if text == ".":
            project = await self.service.require(context.user_id, project_id)
        elif field == "name":
            project = await self.service.update(context.user_id, project_id, name=text)
        elif field == "emoji":
            project = await self.service.update(context.user_id, project_id, emoji=text)
        elif field == "instructions":
            project = await self.service.update(
                context.user_id, project_id, instructions="" if text == "-" else text
            )
        else:
            raise TelegramProjectError("This project draft is no longer active.")
        await state.clear()
        active = await self.service.active(context.user_id)
        await self.rich.send(
            message,
            self.rich.project(project, active=project.id == active.id),
            reply_markup=project_keyboard(project, active=project.id == active.id),
        )

    async def _show_project(
        self,
        message: Message,
        context: RequestContext,
        project: TelegramProject,
        *,
        from_settings: bool = False,
        notice: str | None = None,
    ) -> None:
        active = await self.service.active(context.user_id)
        await self.rich.edit(
            message,
            self.rich.project(project, active=project.id == active.id, notice=notice),
            reply_markup=project_keyboard(
                project, active=project.id == active.id, from_settings=from_settings
            ),
        )
