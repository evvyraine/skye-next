from __future__ import annotations

import io
import re
import sqlite3
import uuid
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import structlog
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from openai import APIError, AsyncOpenAI

from .db import Database
from .models import RequestContext, Scope, Skill
from .rich import RichMessages

log = structlog.get_logger()
MAX_SKILLS = 16
MAX_FILES = 500
MAX_FILE_BYTES = 25 * 1024 * 1024
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SKIP_NAMES = frozenset({".ds_store", "thumbs.db"})


class SkillWizard(StatesGroup):
    upload = State()


class SkillError(ValueError):
    """User-facing skill failure."""


@dataclass(frozen=True, slots=True)
class SkillBundle:
    name: str
    description: str
    filename: str
    archive: bytes
    files: tuple[tuple[str, bytes], ...]

    @property
    def file_count(self) -> int:
        return len(self.files)


def parse_skill_markdown(text: str) -> tuple[str, str]:
    fields = _front_matter(text)
    name = fields.get("name", "").strip().lower()
    description = " ".join(fields.get("description", "").split())
    if not NAME.fullmatch(name):
        raise SkillError("SKILL.md name must be lowercase letters, numbers, and hyphens.")
    if not 1 <= len(description) <= 1024:
        raise SkillError("SKILL.md description must be 1–1024 characters.")
    return name, description


def bundle_from_upload(filename: str, data: bytes, *, max_bytes: int) -> SkillBundle:
    if not data:
        raise SkillError("That file is empty.")
    if len(data) > max_bytes:
        raise SkillError(f"That file is too large (maximum {max_bytes // 1024 // 1024} MB).")
    lowered = filename.lower()
    if lowered.endswith(".md"):
        return _bundle_from_markdown(filename, data)
    if lowered.endswith(".zip"):
        return _bundle_from_zip(filename, data, max_bytes=max_bytes)
    raise SkillError("Upload a .zip skill bundle or a SKILL.md file.")


def skill_paths(archive: bytes) -> tuple[str, ...]:
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        return tuple(
            name
            for name in bundle.namelist()
            if name and not name.endswith("/") and not _skip_member(name)
        )


def _bundle_from_markdown(filename: str, data: bytes) -> SkillBundle:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SkillError("SKILL.md must be UTF-8.") from error
    name, description = parse_skill_markdown(text)
    files = ((f"{name}/SKILL.md", text.encode("utf-8")),)
    return SkillBundle(name, description, f"{name}.zip", _zip_files(files), files)


def _bundle_from_zip(filename: str, data: bytes, *, max_bytes: int) -> SkillBundle:
    members = _read_zip(data, max_bytes=max_bytes)
    skill_md = [path for path in members if PurePosixPath(path).name.lower() == "skill.md"]
    if len(skill_md) != 1:
        raise SkillError("The zip must contain exactly one SKILL.md.")
    try:
        text = members[skill_md[0]].decode("utf-8")
    except UnicodeDecodeError as error:
        raise SkillError("SKILL.md must be UTF-8.") from error
    name, description = parse_skill_markdown(text)
    packed = _with_root(name, members)
    return SkillBundle(name, description, f"{name}.zip", _zip_files(packed), packed)


def _read_zip(data: bytes, *, max_bytes: int) -> dict[str, bytes]:
    buffer = io.BytesIO(data)
    if not zipfile.is_zipfile(buffer):
        raise SkillError("That file is not a valid zip archive.")
    members: dict[str, bytes] = {}
    with zipfile.ZipFile(buffer) as archive:
        infos = [item for item in archive.infolist() if not item.is_dir()]
        if len(infos) > MAX_FILES:
            raise SkillError(f"A skill can include at most {MAX_FILES} files.")
        for item in infos:
            if _skip_member(item.filename):
                continue
            path = _safe_member(item.filename)
            if item.file_size > MAX_FILE_BYTES:
                raise SkillError("A skill file must be at most 25 MB uncompressed.")
            payload = archive.read(item)
            if len(payload) > MAX_FILE_BYTES:
                raise SkillError("A skill file must be at most 25 MB uncompressed.")
            members[path] = payload
    if not members:
        raise SkillError("That zip did not contain any files.")
    total = sum(len(item) for item in members.values())
    if total > max_bytes * 2:
        raise SkillError("That zip expands to more than Skye can store.")
    return members


def _with_root(name: str, members: Mapping[str, bytes]) -> tuple[tuple[str, bytes], ...]:
    roots = {path.split("/", 1)[0] for path in members if path}
    nested = all("/" in path for path in members) and len(roots) == 1
    prefix = f"{name}/"
    files: list[tuple[str, bytes]] = []
    for path, payload in sorted(members.items()):
        relative = path.split("/", 1)[1] if nested else path
        files.append((prefix + relative, payload))
    return tuple(files)


def _zip_files(files: Sequence[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, payload in files:
            archive.writestr(path, payload)
    return buffer.getvalue()


def _safe_member(name: str) -> str:
    path = PurePosixPath(name.replace("\\", "/"))
    parts = [part for part in path.parts if part not in {"", "."}]
    if not parts or path.is_absolute() or ".." in parts:
        raise SkillError("That zip contains an unsafe path.")
    return "/".join(parts)


def _skip_member(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    if "__macosx" in (part.lower() for part in path.parts):
        return True
    return path.name.lower() in SKIP_NAMES


def _front_matter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        raise SkillError("SKILL.md needs YAML front matter with name and description.")
    rest = text[3:].lstrip("\r\n")
    match = re.search(r"\n---[ \t]*\r?\n?", rest)
    if match is None:
        raise SkillError("SKILL.md front matter is not closed.")
    return _yaml_fields(rest[: match.start()])


def _yaml_fields(raw: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    lines = raw.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        index += 1
        if not stripped or stripped.startswith("#") or ":" not in line:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if value in {"|", ">", ">-", "|-"}:
            block: list[str] = []
            while index < len(lines) and (
                not lines[index].strip() or lines[index].startswith((" ", "\t"))
            ):
                block.append(lines[index].strip())
                index += 1
            value = " ".join(part for part in block if part)
        fields[key] = value.strip().strip("\"'")
    return fields


class SkillService:
    def __init__(self, database: Database, client: AsyncOpenAI, max_bytes: int) -> None:
        self.database = database
        self.client = client
        self.max_bytes = max_bytes

    async def list(self, scope: Scope) -> list[Skill]:
        return await self.database.list_skills(scope)

    async def require(self, scope: Scope, skill_id: str) -> Skill:
        skill = await self.database.get_skill(scope, skill_id)
        if skill is None:
            raise SkillError("That skill is not in this chat.")
        return skill

    async def mounted(self, scope: Scope) -> tuple[Skill, ...]:
        return tuple(await self.database.list_skills(scope))

    async def upload(self, scope: Scope, created_by: int, filename: str, data: bytes) -> Skill:
        existing = await self.database.list_skills(scope)
        if len(existing) >= MAX_SKILLS:
            raise SkillError(f"You can add at most {MAX_SKILLS} skills here.")
        bundle = bundle_from_upload(filename, data, max_bytes=self.max_bytes)
        if any(item.name == bundle.name for item in existing):
            raise SkillError(f"A skill named `{bundle.name}` is already in this chat.")
        created = await self._create(bundle)
        openai_id = str(getattr(created, "id", "") or "")
        if not openai_id:
            raise SkillError("OpenAI did not return a skill id.")
        name = str(getattr(created, "name", "") or bundle.name)
        description = str(getattr(created, "description", "") or bundle.description)
        try:
            return await self.database.save_skill(
                Skill(
                    id=uuid.uuid4().hex[:12],
                    scope=scope,
                    openai_skill_id=openai_id,
                    name=name,
                    description=description,
                    filename=bundle.filename,
                    file_count=bundle.file_count,
                    created_by=created_by,
                    created_at="",
                    archive=bundle.archive,
                )
            )
        except sqlite3.IntegrityError as error:
            await self._delete_remote(openai_id)
            raise SkillError(f"A skill named `{name}` is already in this chat.") from error
        except Exception:
            await self._delete_remote(openai_id)
            raise

    async def delete(self, scope: Scope, skill_id: str) -> Skill:
        skill = await self.require(scope, skill_id)
        await self._delete_remote(skill.openai_skill_id)
        removed = await self.database.delete_skill(scope, skill_id)
        if removed is None:
            raise SkillError("That skill is not in this chat.")
        return removed

    async def _create(self, bundle: SkillBundle) -> Any:
        try:
            return await self.client.skills.create(
                files=[(bundle.filename, bundle.archive, "application/zip")]
            )
        except APIError as error:
            log.warning("skill_upload_failed", error=type(error).__name__)
            raise SkillError(_openai_message(error)) from error

    async def _delete_remote(self, openai_skill_id: str) -> None:
        try:
            await self.client.skills.delete(openai_skill_id)
        except APIError as error:
            status = getattr(error, "status_code", None)
            if status == 404:
                return
            log.warning("skill_delete_failed", error=type(error).__name__)
            raise SkillError("Couldn't delete that skill from OpenAI. Try again.") from error


def _openai_message(error: APIError) -> str:
    body = error.body
    if isinstance(body, dict):
        nested = body.get("error")
        message = nested.get("message") if isinstance(nested, dict) else body.get("message")
        if isinstance(message, str) and message:
            return "OpenAI rejected that skill. Check SKILL.md and the bundled files."
    return "OpenAI could not store that skill. Try again."


def skills_keyboard(skills: Sequence[Skill], *, editable: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if editable:
        rows.append([InlineKeyboardButton(text="Upload skill", callback_data="skill:add")])
    rows.extend(
        [InlineKeyboardButton(text=item.name, callback_data=f"skill:open:{item.id}")]
        for item in skills
    )
    rows.append([InlineKeyboardButton(text="‹ Back", callback_data="settings:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def skill_keyboard(skill: Skill, *, editable: bool, confirm: bool = False) -> InlineKeyboardMarkup:
    if confirm:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Delete", callback_data=f"skill:yes:{skill.id}"),
                    InlineKeyboardButton(text="Cancel", callback_data=f"skill:open:{skill.id}"),
                ]
            ]
        )
    rows: list[list[InlineKeyboardButton]] = []
    if editable:
        rows.append([InlineKeyboardButton(text="Delete", callback_data=f"skill:del:{skill.id}")])
    rows.append([InlineKeyboardButton(text="‹ Back", callback_data="skill:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="skill:home")]]
    )


class SkillPanel:
    def __init__(self, service: SkillService, rich: RichMessages, bot: Bot) -> None:
        self.service = service
        self.rich = rich
        self.bot = bot

    async def show_home(
        self,
        message: Message,
        context: RequestContext,
        *,
        editable: bool,
        edit: bool = True,
    ) -> None:
        skills = await self.service.list(context.scope)
        content = self.rich.skills(skills)
        markup = skills_keyboard(skills, editable=editable)
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
        editable: bool,
    ) -> None:
        if action == ["home"]:
            await state.clear()
            await self.show_home(message, context, editable=editable)
            return
        if action == ["add"]:
            if not editable:
                raise PermissionError("Only chat administrators can add skills here.")
            await state.set_state(SkillWizard.upload)
            await state.set_data(
                {
                    "scope_kind": context.scope.kind,
                    "scope_id": context.scope.id,
                    "panel_message_id": message.message_id,
                }
            )
            await self.rich.edit(
                message,
                self.rich.skill_upload_prompt(),
                reply_markup=cancel_keyboard(),
            )
            return
        if len(action) == 2 and action[0] == "open":
            skill = await self.service.require(context.scope, action[1])
            await self._show_skill(message, skill, editable=editable)
            return
        if len(action) == 2 and action[0] == "del":
            if not editable:
                raise PermissionError("Only chat administrators can delete skills here.")
            skill = await self.service.require(context.scope, action[1])
            await self.rich.edit(
                message,
                self.rich.skill_delete_confirm(skill.name),
                reply_markup=skill_keyboard(skill, editable=True, confirm=True),
            )
            return
        if len(action) == 2 and action[0] == "yes":
            if not editable:
                raise PermissionError("Only chat administrators can delete skills here.")
            await self.service.delete(context.scope, action[1])
            await self.show_home(message, context, editable=True)

    async def handle_wizard(
        self, message: Message, context: RequestContext, state: FSMContext
    ) -> None:
        data = await state.get_data()
        if (data.get("scope_kind"), data.get("scope_id")) != (
            context.scope.kind,
            context.scope.id,
        ):
            await state.clear()
            raise SkillError("This skill upload belongs to another chat.")
        filename, payload = await self._upload(message)
        skill = await self.service.upload(context.scope, context.user_id, filename, payload)
        await state.clear()
        await self.rich.send(
            message,
            self.rich.skill(skill, files=skill_paths(skill.archive)),
            reply_markup=skill_keyboard(skill, editable=True),
        )

    async def _show_skill(self, message: Message, skill: Skill, *, editable: bool) -> None:
        files = skill_paths(skill.archive) if skill.archive else ()
        await self.rich.edit(
            message,
            self.rich.skill(skill, files=files),
            reply_markup=skill_keyboard(skill, editable=editable),
        )

    async def _upload(self, message: Message) -> tuple[str, bytes]:
        if message.document:
            filename = message.document.file_name or "skill.zip"
            if message.document.file_size and message.document.file_size > self.service.max_bytes:
                raise SkillError(
                    f"That file is too large (maximum {self.service.max_bytes // 1024 // 1024} MB)."
                )
            destination = io.BytesIO()
            await self.bot.download(message.document, destination=destination)
            return filename, destination.getvalue()
        text = (message.text or "").strip()
        if text.startswith("---"):
            return "SKILL.md", text.encode("utf-8")
        raise SkillError("Send a `.zip` archive or a `SKILL.md` file.")


def hosted_skill_refs(skills: Sequence[Skill]) -> list[dict[str, str]]:
    return [{"type": "skill_reference", "skill_id": item.openai_skill_id} for item in skills]
