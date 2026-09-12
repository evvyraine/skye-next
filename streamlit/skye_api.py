# ruff: noqa: E501
"""Thin client over the existing aiohttp API (variant A).

The Streamlit app never touches SQLite. It reads the existing ``skye_session``
cookie and talks to ``/api/*`` exactly like the React client does, so the bot
process stays the single writer and the agent runtime is never duplicated.

``DemoClient`` mirrors the same surface with in-memory data so the UI can be
previewed without a running backend.
"""

from __future__ import annotations

import json
import random
import struct
import time
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class SkyeAPIError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True, slots=True)
class User:
    id: int
    name: str
    username: str | None = None

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> User:
        return cls(
            id=int(data["id"]),
            name=str(data.get("name") or "User"),
            username=data.get("username"),
        )


@dataclass(frozen=True, slots=True)
class Project:
    id: str
    kind: str
    name: str
    instructions: str
    icon: str
    color: str
    pinned: bool
    last_message_preview: str
    last_message_at: str | None
    created_at: str
    updated_at: str
    deletable: bool

    @property
    def is_inbox(self) -> bool:
        return self.kind == "skye"

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> Project:
        return cls(
            id=str(data["id"]),
            kind=str(data.get("kind") or "custom"),
            name=str(data.get("name") or "Untitled"),
            instructions=str(data.get("instructions") or ""),
            icon=str(data.get("icon") or "sparkles"),
            color=str(data.get("color") or "zinc"),
            pinned=bool(data.get("pinned")),
            last_message_preview=str(data.get("last_message_preview") or ""),
            last_message_at=data.get("last_message_at"),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            deletable=bool(data.get("deletable", data.get("kind") != "skye")),
        )


@dataclass(frozen=True, slots=True)
class Message:
    id: str
    project_id: str
    role: str
    text: str
    tool_name: str | None
    tool_status: str | None
    file_ids: tuple[str, ...]
    created_at: str
    tool_args: str | None = None
    tool_output: str | None = None

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> Message:
        return cls(
            id=str(data["id"]),
            project_id=str(data.get("project_id") or ""),
            role=str(data.get("role") or "assistant"),
            text=str(data.get("text") or ""),
            tool_name=data.get("tool_name"),
            tool_status=data.get("tool_status"),
            file_ids=tuple(str(item) for item in data.get("file_ids") or ()),
            created_at=str(data.get("created_at") or ""),
            tool_args=data.get("tool_args"),
            tool_output=data.get("tool_output"),
        )


@dataclass(frozen=True, slots=True)
class ChatFile:
    id: str
    project_id: str
    filename: str
    mime: str
    size: int
    kind: str
    url: str
    thumbnail_url: str | None
    created_at: str

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> ChatFile:
        return cls(
            id=str(data["id"]),
            project_id=str(data.get("project_id") or ""),
            filename=str(data.get("filename") or "file"),
            mime=str(data.get("mime") or "application/octet-stream"),
            size=int(data.get("size") or 0),
            kind=str(data.get("kind") or "upload"),
            url=str(data.get("url") or ""),
            thumbnail_url=data.get("thumbnail_url"),
            created_at=str(data.get("created_at") or ""),
        )


@dataclass(frozen=True, slots=True)
class Upload:
    name: str
    mime: str
    data: bytes


@dataclass(frozen=True, slots=True)
class StreamEvent:
    kind: str
    data: dict[str, Any]


class Client(Protocol):
    """The surface the app uses. Implemented live and in-memory."""

    mode: str

    def me(self) -> tuple[User | None, bool]: ...

    def list_projects(self) -> list[Project]: ...

    def create_project(self, *, name: str, instructions: str, icon: str, color: str) -> Project: ...

    def update_project(self, project_id: str, **fields: object) -> Project: ...

    def delete_project(self, project_id: str) -> None: ...

    def pin_project(self, project_id: str) -> Project: ...

    def reset_project(self, project_id: str) -> Project: ...

    def list_messages(self, project_id: str) -> tuple[list[Message], list[ChatFile]]: ...

    def stream_reply(
        self, project_id: str, text: str, uploads: Iterable[Upload]
    ) -> Iterator[StreamEvent]: ...

    def file_bytes(self, file_id: str) -> bytes | None: ...

    def thumbnail_bytes(self, file_id: str) -> bytes | None: ...

    def search(self, query: str) -> list[tuple[Project, Message]]: ...

    def logout(self) -> None: ...


# --------------------------------------------------------------------------- live


class SkyeAPI:
    """HTTP + SSE client for the existing web API."""

    mode = "live"

    def __init__(self, base_url: str, session_id: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Cookie": f"skye_session={session_id}"},
            timeout=httpx.Timeout(30.0, read=900.0),
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise SkyeAPIError(_error_text(response), response.status_code)
        return response

    def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return dict(self._request(method, path, **kwargs).json())

    def me(self) -> tuple[User | None, bool]:
        response = self._client.get("/api/me")
        if response.status_code == 401:
            return None, False
        if response.status_code >= 400:
            raise SkyeAPIError(_error_text(response), response.status_code)
        data = response.json()
        user = data.get("user")
        return (User.from_payload(user) if user else None), bool(data.get("allowed"))

    def list_projects(self) -> list[Project]:
        data = self._json("GET", "/api/projects")
        return [Project.from_payload(item) for item in data.get("projects") or ()]

    def create_project(self, *, name: str, instructions: str, icon: str, color: str) -> Project:
        data = self._json(
            "POST",
            "/api/projects",
            json={"name": name, "instructions": instructions, "icon": icon, "color": color},
        )
        return Project.from_payload(data["project"])

    def update_project(self, project_id: str, **fields: object) -> Project:
        data = self._json("PATCH", f"/api/projects/{project_id}", json=fields)
        return Project.from_payload(data["project"])

    def delete_project(self, project_id: str) -> None:
        self._request("DELETE", f"/api/projects/{project_id}")

    def pin_project(self, project_id: str) -> Project:
        data = self._json("POST", f"/api/projects/{project_id}/pin")
        return Project.from_payload(data["project"])

    def reset_project(self, project_id: str) -> Project:
        data = self._json("POST", f"/api/projects/{project_id}/reset")
        return Project.from_payload(data["project"])

    def list_messages(self, project_id: str) -> tuple[list[Message], list[ChatFile]]:
        data = self._json("GET", f"/api/projects/{project_id}/messages")
        messages = [Message.from_payload(item) for item in data.get("messages") or ()]
        files = [ChatFile.from_payload(item) for item in data.get("files") or ()]
        return messages, files

    def stream_reply(
        self, project_id: str, text: str, uploads: Iterable[Upload]
    ) -> Iterator[StreamEvent]:
        files = [(item.name, item.mime, item.data) for item in uploads]
        if files:
            kwargs: dict[str, Any] = {
                "data": {"text": text},
                "files": [("files", (name, data, mime)) for name, mime, data in files],
            }
        else:
            kwargs = {"json": {"text": text}}
        with self._client.stream(
            "POST", f"/api/projects/{project_id}/messages", **kwargs
        ) as response:
            if response.status_code >= 400:
                response.read()
                raise SkyeAPIError(_error_text(response), response.status_code)
            event_name: str | None = None
            for line in response.iter_lines():
                if not line:
                    event_name = None
                    continue
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    payload = json.loads(line.split(":", 1)[1].strip())
                    yield StreamEvent(event_name or "message", payload)

    def file_bytes(self, file_id: str) -> bytes | None:
        response = self._client.get(f"/api/files/{file_id}")
        return response.content if response.status_code == 200 else None

    def thumbnail_bytes(self, file_id: str) -> bytes | None:
        response = self._client.get(f"/api/files/{file_id}/thumbnail")
        return response.content if response.status_code == 200 else None

    def search(self, query: str) -> list[tuple[Project, Message]]:
        data = self._json("GET", "/api/search", params={"q": query})
        results: list[tuple[Project, Message]] = []
        for item in data.get("messages") or ():
            results.append(
                (Project.from_payload(item["project"]), Message.from_payload(item["message"]))
            )
        return results

    def logout(self) -> None:
        self._request("POST", "/auth/logout")


def _error_text(response: httpx.Response) -> str:
    try:
        return response.text.strip() or f"Request failed ({response.status_code})."
    except Exception:  # pragma: no cover - defensive
        return f"Request failed ({response.status_code})."


# --------------------------------------------------------------------------- demo


REPLIES: dict[str, str] = {
    "привет": "Привет. Я рядом. Чем займёмся?",
    "hello": "Hi. I'm here — what are we working on?",
}


class DemoClient:
    """In-memory stand-in for the live client, for previewing the UI."""

    mode = "demo"

    def __init__(self) -> None:
        self._clock = time.time() - 86_400
        self.projects: dict[str, Project] = {}
        self.messages: dict[str, list[Message]] = {}
        self.files: dict[str, ChatFile] = {}
        self._counter = 0
        self._seed()

    # -- seed -----------------------------------------------------------------

    def _now(self) -> str:
        self._clock += random.randint(3, 90)
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._clock))

    def _new_id(self) -> str:
        self._counter += 1
        return f"demo{self._counter:04d}"

    def _add_project(
        self,
        name: str,
        *,
        kind: str = "custom",
        icon: str = "sparkles",
        color: str = "violet",
        pinned: bool = False,
        instructions: str = "",
    ) -> Project:
        project = Project(
            id=self._new_id(),
            kind=kind,
            name=name,
            instructions=instructions,
            icon=icon,
            color=color,
            pinned=pinned,
            last_message_preview="",
            last_message_at=None,
            created_at=self._now(),
            updated_at=self._now(),
            deletable=kind != "skye",
        )
        self.projects[project.id] = project
        self.messages[project.id] = []
        return project

    def _add_message(
        self,
        project: Project,
        role: str,
        text: str,
        *,
        tool_name: str | None = None,
        tool_status: str | None = None,
        tool_args: str | None = None,
        tool_output: str | None = None,
        file_ids: tuple[str, ...] = (),
    ) -> Message:
        message = Message(
            id=self._new_id(),
            project_id=project.id,
            role=role,
            text=text,
            tool_name=tool_name,
            tool_status=tool_status,
            file_ids=file_ids,
            created_at=self._now(),
            tool_args=tool_args,
            tool_output=tool_output,
        )
        self.messages[project.id].append(message)
        self.projects[project.id] = _replace(
            project, last_message_preview=text[:80], last_message_at=message.created_at
        )
        return message

    def _add_file(
        self, project: Project, filename: str, mime: str, data: bytes, kind: str
    ) -> ChatFile:
        file = ChatFile(
            id=self._new_id(),
            project_id=project.id,
            filename=filename,
            mime=mime,
            size=len(data),
            kind=kind,
            url="",
            thumbnail_url=None,
            created_at=self._now(),
        )
        self.files[file.id] = file
        _FILE_BYTES[file.id] = data
        return file

    def _seed(self) -> None:
        inbox = self._add_project(
            "Inbox", kind="skye", icon="chat-bubble-left-right", color="zinc", pinned=False
        )
        self._add_message(inbox, "user", "Привет! Собери короткий план на неделю.")
        self._add_message(
            inbox,
            "tool",
            "Searching memories",
            tool_name="recall",
            tool_status="done",
            tool_args='{"query": "weekly plan"}',
            tool_output="3 memories",
        )
        self._add_message(
            inbox,
            "assistant",
            "Готово. Три главных вещи:\n\n1. Закрыть ревью.\n2. Подготовить демо.\n3. Оставить пятницу свободной.",
        )

        notes = self._add_project(
            "Product notes",
            icon="light-bulb",
            color="violet",
            pinned=True,
            instructions="Ты продуктовый редактор. Пиши коротко и по делу.",
        )
        self._add_message(notes, "user", "Сделай выжимку фидбека по вебу.")
        self._add_message(
            notes,
            "assistant",
            "Главное из фидбека:\n\n- Звуки в приложении раздражают.\n- Мобильные жесты не работают.\n- Хочется меньше «шелухи».",
        )

        self._add_project("Trip planning", icon="globe-alt", color="teal")
        self._add_project("Code snippets", icon="code-bracket", color="blue")
        self._add_project("Reading", icon="academic-cap", color="amber")

    # -- Client surface -------------------------------------------------------

    def me(self) -> tuple[User | None, bool]:
        return User(id=1, name="Alex", username="alex"), True

    def list_projects(self) -> list[Project]:
        items = list(self.projects.values())
        # Inbox always first, then pinned, then name.
        items.sort(
            key=lambda item: (
                0 if item.is_inbox else 1,
                0 if item.pinned else 1,
                item.name.lower(),
            )
        )
        return items

    def create_project(self, *, name: str, instructions: str, icon: str, color: str) -> Project:
        project = self._add_project(
            name.strip() or "Untitled",
            icon=icon,
            color=color,
            instructions=instructions.strip(),
        )
        return project

    def update_project(self, project_id: str, **fields: object) -> Project:
        project = self.projects[project_id]
        updated = _replace(project, **fields)  # type: ignore[arg-type]
        self.projects[project_id] = updated
        return updated

    def delete_project(self, project_id: str) -> None:
        self.projects.pop(project_id, None)
        self.messages.pop(project_id, None)

    def pin_project(self, project_id: str) -> Project:
        project = self.projects[project_id]
        return self.update_project(project_id, pinned=not project.pinned)

    def reset_project(self, project_id: str) -> Project:
        self.messages[project_id] = []
        project = self.projects[project_id]
        updated = _replace(project, last_message_preview="", last_message_at=None)
        self.projects[project_id] = updated
        return updated

    def list_messages(self, project_id: str) -> tuple[list[Message], list[ChatFile]]:
        files = [item for item in self.files.values() if item.project_id == project_id]
        return list(self.messages.get(project_id, [])), files

    def stream_reply(self, project_id: str, text: str, uploads: Iterable[Upload]) -> Iterator[StreamEvent]:
        project = self.projects[project_id]
        uploads = list(uploads)
        file_ids: list[str] = []
        for upload in uploads:
            is_image = upload.mime.startswith("image/")
            kind = "image" if is_image else "upload"
            saved = self._add_file(project, upload.name, upload.mime, upload.data, kind)
            file_ids.append(saved.id)
            yield StreamEvent("file", _file_payload(saved))
        user = self._add_message(
            project, "user", text or ", ".join(item.name for item in uploads), file_ids=tuple(file_ids)
        )
        yield StreamEvent("user", _message_payload(user))

        lowered = text.lower().strip()
        if lowered in REPLIES:
            answer = REPLIES[lowered]
        elif uploads:
            answer = "Принял файл. Посмотрел и оставил заметку в проекте."
        else:
            answer = (
                "Понял. Вот как я бы подошёл:\n\n"
                "1. Разобрать задачу на части.\n"
                "2. Сделать самый маленький шаг.\n"
                "3. Проверить результат и записать вывод.\n\n"
                "Скажи, с чего начнём."
            )

        if random.random() < 0.7:
            tool_id = self._new_id()
            query = json.dumps({"query": text[:60] or "weekly plan"}, ensure_ascii=False)
            yield StreamEvent("tool", {
                "id": tool_id,
                "name": "web_search",
                "label": "Searching the web",
                "status": "running",
                "args": query,
                "output": None,
            })
            time.sleep(0.6)
            yield StreamEvent("tool", {
                "id": tool_id,
                "name": "web_search",
                "label": "Searching the web",
                "status": "done",
                "args": query,
                "output": "3 relevant sources",
            })

        if uploads and any(item.mime.startswith("image/") for item in uploads):
            image = next(item for item in uploads if item.mime.startswith("image/"))
            saved = self._add_file(project, "preview.png", "image/png", _gradient_png(), "image")
            yield StreamEvent("image", _file_payload(saved))
            file_ids.append(saved.id)

        for chunk in _split_stream(answer):
            time.sleep(0.12)
            message = self._add_message(project, "assistant", chunk)
            yield StreamEvent("assistant", _message_payload(message))

        if random.random() < 0.35:
            saved = self._add_file(
                project, "notes.md", "text/markdown", b"# Notes\n\nGenerated in demo mode.\n", "document"
            )
            yield StreamEvent("file", _file_payload(saved))
        yield StreamEvent("done", {"text": answer})

    def file_bytes(self, file_id: str) -> bytes | None:
        return _FILE_BYTES.get(file_id)

    def thumbnail_bytes(self, file_id: str) -> bytes | None:
        return _FILE_BYTES.get(file_id)

    def search(self, query: str) -> list[tuple[Project, Message]]:
        needle = query.strip().lower()
        if not needle:
            return []
        results: list[tuple[Project, Message]] = []
        for project in self.projects.values():
            for message in self.messages.get(project.id, []):
                if needle in message.text.lower():
                    results.append((project, message))
        return results[:20]

    def logout(self) -> None:
        return None


_FILE_BYTES: dict[str, bytes] = {}


def _replace(project: Project, **fields: object) -> Project:
    data = {
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
        "deletable": project.deletable,
    }
    data.update(fields)
    return Project(**data)  # type: ignore[arg-type]


def _message_payload(message: Message) -> dict[str, Any]:
    return {
        "id": message.id,
        "project_id": message.project_id,
        "role": message.role,
        "text": message.text,
        "tool_name": message.tool_name,
        "tool_status": message.tool_status,
        "file_ids": list(message.file_ids),
        "created_at": message.created_at,
        "tool_args": message.tool_args,
        "tool_output": message.tool_output,
    }


def _file_payload(file: ChatFile) -> dict[str, Any]:
    return {
        "id": file.id,
        "project_id": file.project_id,
        "filename": file.filename,
        "mime": file.mime,
        "size": file.size,
        "kind": file.kind,
        "url": file.url,
        "thumbnail_url": file.thumbnail_url,
        "created_at": file.created_at,
    }


def _split_stream(text: str) -> list[str]:
    """Split a reply into 1–2 sentence bubbles, like the live runtime does."""
    parts = [chunk.strip() for chunk in text.split("\n\n") if chunk.strip()]
    if len(parts) <= 1:
        sentences = [item.strip() for item in text.replace("! ", ". ").split(". ") if item.strip()]
        parts = [f"{item}." for item in sentences[:2]] or [text]
    return parts


def _gradient_png(width: int = 640, height: int = 400) -> bytes:
    """Small pure-stdlib PNG so the demo can show a generated image."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            t = x / (width - 1)
            lift = (y / (height - 1)) * 0.15
            red = int((0x74 + (0x4E - 0x74) * t) * (1 - lift))
            green = int((0x00 + (0xA8 - 0x00) * t) * (1 - lift))
            blue = int((0xB8 + (0xDE - 0xB8) * t) * (1 - lift))
            raw += bytes((red, green, blue))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )
