from __future__ import annotations

import asyncio
import secrets
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import cast

from PIL import Image, ImageOps, UnidentifiedImageError

from .db import Database
from .models import (
    ToolStatus,
    WebFile,
    WebFileKind,
    WebMessage,
    WebMessageRole,
    WebProject,
    WebSession,
)

PROJECT_ICONS: tuple[str, ...] = (
    "cloud",
    "chat-bubble-left-right",
    "code-bracket",
    "cog-6-tooth",
    "briefcase",
    "academic-cap",
    "heart",
    "sparkles",
    "globe-alt",
    "paint-brush",
    "beaker",
    "musical-note",
    "camera",
    "folder",
    "light-bulb",
    "star",
)
PROJECT_COLORS: tuple[str, ...] = (
    "zinc",
    "slate",
    "stone",
    "neutral",
    "red",
    "orange",
    "amber",
    "green",
    "teal",
    "blue",
    "indigo",
    "violet",
    "pink",
)
MAX_PROJECTS = 50
SESSION_DAYS = 30
THUMBNAIL_SIZE = (640, 640)
THUMBNAIL_QUALITY = 78


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class ProjectService:
    def __init__(
        self,
        database: Database,
        files_path: Path,
    ) -> None:
        self.database = database
        self.files_path = files_path
        self._conversation_locks: defaultdict[tuple[int, str], asyncio.Lock] = defaultdict(
            asyncio.Lock
        )

    async def ensure_skye(self, user_id: int) -> WebProject:
        existing = await self.database.skye_web_project(user_id)
        if existing is not None:
            return existing
        return await self.database.create_web_project(
            WebProject(
                id=new_id(),
                user_id=user_id,
                kind="skye",
                name="Skye",
                instructions="",
                icon="cloud",
                color="zinc",
                pinned=True,
                openai_conversation_id=None,
                last_message_preview="",
                last_message_at=None,
                created_at="",
                updated_at="",
            )
        )

    async def list(self, user_id: int) -> list[WebProject]:
        await self.ensure_skye(user_id)
        return await self.database.list_web_projects(user_id)

    async def require(self, user_id: int, project_id: str) -> WebProject:
        project = await self.database.web_project(user_id, project_id)
        if project is None:
            raise LookupError("Project not found.")
        return project

    async def create(
        self,
        user_id: int,
        *,
        name: str,
        instructions: str = "",
        icon: str = "sparkles",
        color: str = "zinc",
    ) -> WebProject:
        await self.ensure_skye(user_id)
        current = await self.database.list_web_projects(user_id)
        if len(current) >= MAX_PROJECTS:
            raise ValueError(f"You can keep up to {MAX_PROJECTS} projects.")
        return await self.database.create_web_project(
            WebProject(
                id=new_id(),
                user_id=user_id,
                kind="custom",
                name=self._name(name),
                instructions=self._instructions(instructions),
                icon=self._icon(icon),
                color=self._color(color),
                pinned=False,
                openai_conversation_id=None,
                last_message_preview="",
                last_message_at=None,
                created_at="",
                updated_at="",
            )
        )

    async def update(
        self,
        user_id: int,
        project_id: str,
        *,
        name: str | None = None,
        instructions: str | None = None,
        icon: str | None = None,
        color: str | None = None,
        pinned: bool | None = None,
    ) -> WebProject:
        project = await self.require(user_id, project_id)
        if project.kind == "skye" and name is not None and name.strip() != "Skye":
            name = "Skye"
        updated = await self.database.update_web_project(
            user_id,
            project_id,
            name=None if name is None else self._name(name),
            instructions=None if instructions is None else self._instructions(instructions),
            icon=None if icon is None else self._icon(icon),
            color=None if color is None else self._color(color),
            pinned=pinned,
        )
        if updated is None:
            raise LookupError("Project not found.")
        return updated

    async def delete(self, user_id: int, project_id: str) -> WebProject:
        files = await self.database.list_web_files(user_id, project_id)
        project = await self.database.delete_web_project(user_id, project_id)
        if project is None:
            raise LookupError("Project not found.")
        await self._delete_conversation(project.openai_conversation_id)
        for item in files:
            path = self._file_path(user_id, item.id)
            path.unlink(missing_ok=True)
            self._thumbnail_path(user_id, item.id).unlink(missing_ok=True)
        return project

    async def reset(self, user_id: int, project_id: str) -> WebProject:
        project = await self.require(user_id, project_id)
        await self._delete_conversation(project.openai_conversation_id)
        await self.database.set_web_conversation(user_id, project_id, None)
        await self.database.clear_web_messages(user_id, project_id)
        await self.database.touch_web_project(user_id, project_id, "")
        return await self.require(user_id, project_id)

    async def conversation_id(self, project: WebProject) -> str:
        key = project.user_id, project.id
        async with self._conversation_locks[key]:
            current = await self.require(project.user_id, project.id)
            if current.openai_conversation_id:
                return current.openai_conversation_id
            conversation_id = f"web-project:{current.id}"
            await self.database.set_web_conversation(current.user_id, current.id, conversation_id)
            return conversation_id

    async def add_message(
        self,
        user_id: int,
        project_id: str,
        *,
        role: WebMessageRole,
        text: str = "",
        tool_name: str | None = None,
        tool_status: str | None = None,
        file_ids: tuple[str, ...] = (),
    ) -> WebMessage:
        status = tool_status if tool_status in {"running", "done"} else None
        message = await self.database.add_web_message(
            WebMessage(
                id=new_id(),
                project_id=project_id,
                user_id=user_id,
                role=role,
                text=text,
                tool_name=tool_name,
                tool_status=cast(ToolStatus | None, status),
                file_ids=file_ids,
                created_at="",
            )
        )
        if role in {"user", "assistant"} and text:
            await self.database.touch_web_project(user_id, project_id, text)
        return message

    async def save_file(
        self,
        user_id: int,
        project_id: str,
        *,
        filename: str,
        mime: str,
        data: bytes,
        kind: WebFileKind,
    ) -> WebFile:
        file_id = new_id()
        path = self._file_path(user_id, file_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return await self.database.add_web_file(
            WebFile(
                id=file_id,
                user_id=user_id,
                project_id=project_id,
                filename=filename,
                mime=mime,
                size=len(data),
                kind=kind,
                created_at="",
            )
        )

    def file_bytes(self, user_id: int, file_id: str) -> bytes | None:
        path = self._file_path(user_id, file_id)
        if not path.is_file():
            return None
        return path.read_bytes()

    def thumbnail_bytes(self, user_id: int, file_id: str) -> bytes | None:
        thumbnail = self._thumbnail_path(user_id, file_id)
        if thumbnail.is_file():
            return thumbnail.read_bytes()
        source = self._file_path(user_id, file_id)
        if not source.is_file():
            return None
        try:
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened)
                image.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
                if image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                output = BytesIO()
                image.save(
                    output,
                    format="WEBP",
                    quality=THUMBNAIL_QUALITY,
                    method=6,
                )
        except OSError, UnidentifiedImageError:
            return None
        data = output.getvalue()
        thumbnail.write_bytes(data)
        return data

    def session_cookie(self, origin: str | None) -> dict[str, object]:
        secure = bool(origin and origin.startswith("https://"))
        return {
            "httponly": True,
            "secure": secure,
            "samesite": "Lax",
            "path": "/",
            "max_age": SESSION_DAYS * 24 * 60 * 60,
        }

    async def create_session(
        self, user_id: int, display_name: str, username: str | None
    ) -> WebSession:
        now = datetime.now(UTC)
        session = WebSession(
            id=secrets.token_urlsafe(32),
            user_id=user_id,
            display_name=display_name.strip() or "User",
            username=username,
            created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
            expires_at=(now + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S"),
        )
        return await self.database.create_web_session(session)

    async def _delete_conversation(self, conversation_id: str | None) -> None:
        if not conversation_id:
            return
        await self.database.clear_session(conversation_id)

    def _file_path(self, user_id: int, file_id: str) -> Path:
        return self.files_path / str(user_id) / file_id

    def _thumbnail_path(self, user_id: int, file_id: str) -> Path:
        return self.files_path / str(user_id) / f"{file_id}.thumbnail.webp"

    @staticmethod
    def _name(name: str) -> str:
        cleaned = " ".join(name.split())
        if not 1 <= len(cleaned) <= 64:
            raise ValueError("Project name must be 1–64 characters.")
        return cleaned

    @staticmethod
    def _instructions(instructions: str) -> str:
        text = instructions.strip()
        if len(text) > 12_000:
            raise ValueError("Instructions must be at most 12,000 characters.")
        return text

    @staticmethod
    def _icon(icon: str) -> str:
        if icon not in PROJECT_ICONS:
            raise ValueError("Unknown project icon.")
        return icon

    @staticmethod
    def _color(color: str) -> str:
        if color not in PROJECT_COLORS:
            raise ValueError("Unknown project color.")
        return color


def project_payload(project: WebProject) -> dict[str, object]:
    return {
        "id": project.id,
        "kind": project.kind,
        "name": project.name,
        "instructions": project.instructions,
        "icon": project.icon,
        "color": project.color,
        "pinned": project.pinned,
        "last_message_preview": project.last_message_preview,
        "last_message_at": project.last_message_at,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "deletable": project.kind != "skye",
    }


def message_payload(message: WebMessage) -> dict[str, object]:
    return {
        "id": message.id,
        "project_id": message.project_id,
        "role": message.role,
        "text": message.text,
        "tool_name": message.tool_name,
        "tool_status": message.tool_status,
        "file_ids": list(message.file_ids),
        "created_at": message.created_at,
    }


def file_payload(file: WebFile) -> dict[str, object]:
    return {
        "id": file.id,
        "project_id": file.project_id,
        "filename": file.filename,
        "mime": file.mime,
        "size": file.size,
        "kind": file.kind,
        "url": f"/api/files/{file.id}",
        "thumbnail_url": f"/api/files/{file.id}/thumbnail"
        if file.mime.startswith("image/")
        else None,
        "created_at": file.created_at,
    }
