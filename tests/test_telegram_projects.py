from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User

from skye.db import Database
from skye.models import RequestContext, TelegramProject
from skye.projects import ProjectService
from skye.telegram import CATCHUP_PROMPT, TelegramApp
from skye.telegram_projects import (
    DEFAULT_EMOJI,
    PROJECT_KEYBOARD_CATCHUP,
    PROJECT_KEYBOARD_PROJECTS,
    TelegramProjectError,
    TelegramProjectService,
    parse_leading_emoji,
    parse_project_callback,
    parse_standalone_emoji,
    project_reply_keyboard,
    projects_keyboard,
)


@pytest.fixture
async def database(tmp_path: Path):
    value = Database(tmp_path / "skye.db", "gpt-5.6-luna", "medium")
    await value.open()
    try:
        yield value
    finally:
        await value.close()


class FakeConversations:
    def __init__(self) -> None:
        self.created: list[Any] = []
        self.deleted: list[str] = []
        self._n = 0
        self.items = SimpleNamespace(list=AsyncMock(return_value=SimpleNamespace(data=[])))

    async def create(self, metadata: dict[str, str] | None = None) -> SimpleNamespace:
        self._n += 1
        conversation = SimpleNamespace(id=f"conv_tg_{self._n}", metadata=metadata or {})
        self.created.append(conversation)
        return conversation

    async def delete(self, conversation_id: str) -> None:
        self.deleted.append(conversation_id)


def client() -> Any:
    return SimpleNamespace(conversations=FakeConversations())


def service(database: Database, openai: Any | None = None) -> TelegramProjectService:
    return TelegramProjectService(database, openai or client())


def private_message(text: str, *, user_id: int = 1) -> Message:
    return Message(
        message_id=1,
        date=0,
        chat=Chat(id=user_id, type="private", first_name="Alice"),
        from_user=User(id=user_id, is_bot=False, first_name="Alice"),
        text=text,
    )


def test_parse_leading_emoji() -> None:
    assert parse_leading_emoji("🧠 Research") == ("🧠", "Research")
    assert parse_leading_emoji("Research") == (None, "Research")
    assert parse_leading_emoji("☁️ Skye") == ("☁️", "Skye")
    assert parse_leading_emoji("✨") == ("✨", "")
    assert parse_standalone_emoji("hello") is None
    assert parse_standalone_emoji("📁") == "📁"


def test_parse_project_callback() -> None:
    assert parse_project_callback("settings:back") is None
    assert parse_project_callback("proj:open:abc123") == ("open", "abc123")
    assert parse_project_callback("proj:home:s") == ("home", "s")
    assert parse_project_callback("proj:page:2:s") == ("page", "2", "s")


async def test_skye_project_is_created_once_and_cannot_be_deleted(
    database: Database,
) -> None:
    projects = service(database)
    first = await projects.ensure_skye(42)
    second = await projects.ensure_skye(42)

    assert first.id == second.id
    assert first.kind == "skye"
    assert first.name == "Skye"
    assert first.emoji == DEFAULT_EMOJI
    with pytest.raises(PermissionError):
        await projects.delete(42, first.id)
    renamed = await projects.update(42, first.id, name="Other")
    assert renamed.name == "Skye"


async def test_projects_are_isolated_by_user(database: Database) -> None:
    projects = service(database)
    alice = await projects.create(1, name="Frontend", emoji="💻")
    await projects.ensure_skye(2)

    assert await database.telegram_project(2, alice.id) is None
    listed = await projects.list(2)
    assert [item.name for item in listed] == ["Skye"]


async def test_existing_private_conversation_migrates_onto_skye(
    database: Database,
) -> None:
    await database.save_conversation(7, 0, "conv_old")
    projects = service(database)

    skye = await projects.ensure_skye(7)

    assert skye.openai_conversation_id == "conv_old"
    assert await database.conversation_id(7, 0) is None
    assert (await projects.active(7)).id == skye.id


async def test_selecting_a_project_changes_the_active_conversation(
    database: Database,
) -> None:
    openai = client()
    projects = service(database, openai)
    skye = await projects.ensure_skye(1)
    custom = await projects.create(1, name="Research", emoji="🧠")

    skye_id = await projects.conversation_id(skye)
    custom_id = await projects.conversation_id(custom)
    assert skye_id != custom_id
    assert all(item.metadata.get("telegram_project") for item in openai.conversations.created)

    selected = await projects.select(1, skye.id)
    assert (await projects.active(1)).id == selected.id
    assert await projects.conversation_id(await projects.active(1)) == skye_id


async def test_web_and_telegram_conversation_ids_never_match(
    database: Database, tmp_path: Path
) -> None:
    openai = client()
    telegram = service(database, openai)
    web = ProjectService(database, openai, tmp_path / "web")
    telegram_project = await telegram.create(1, name="Notes", emoji="📁")
    web_project = await web.create(1, name="Notes")

    telegram_id = await telegram.conversation_id(telegram_project)
    web_id = await web.conversation_id(web_project)

    assert telegram_id != web_id
    telegram_meta = openai.conversations.created[0].metadata
    web_meta = openai.conversations.created[1].metadata
    assert "telegram_project" in telegram_meta
    assert "web_project" in web_meta


async def test_delete_active_falls_back_to_skye(database: Database) -> None:
    projects = service(database)
    skye = await projects.ensure_skye(1)
    custom = await projects.create(1, name="Temp")
    assert (await projects.active(1)).id == custom.id

    await projects.delete(1, custom.id)

    active = await projects.active(1)
    assert active.id == skye.id
    assert await database.telegram_project(1, custom.id) is None


async def test_groups_do_not_use_telegram_projects(database: Database) -> None:
    projects = service(database)
    await projects.ensure_skye(42)
    group = RequestContext(-100, "supergroup", user_id=42)

    assert group.scope.kind == "chat"
    assert await database.telegram_project(-100, (await projects.active(42)).id) is None
    assert await database.list_telegram_projects(-100) == []


async def test_create_rejects_empty_names(database: Database) -> None:
    projects = service(database)
    with pytest.raises(TelegramProjectError):
        await projects.create(1, name="  ")


async def test_project_reply_keyboard_uses_plain_labels() -> None:
    project = TelegramProject(
        id="abc",
        user_id=1,
        kind="custom",
        name="Research",
        emoji="🧠",
        instructions="Prefer TypeScript.",
        openai_conversation_id=None,
        created_at="",
        updated_at="",
    )
    markup = project_reply_keyboard(project)
    labels = [button.text for row in markup.keyboard for button in row]
    assert labels == [PROJECT_KEYBOARD_PROJECTS, PROJECT_KEYBOARD_CATCHUP]
    assert labels == ["💼 Projects", "📝 Catch up"]
    assert markup.input_field_placeholder == "🧠 Research"
    assert markup.is_persistent is True


async def test_projects_keyboard_marks_the_active_project(database: Database) -> None:
    projects = service(database)
    skye = await projects.ensure_skye(1)
    custom = await projects.create(1, name="Lab", emoji="🧪")
    listed = await projects.list(1)
    markup = projects_keyboard(listed, custom.id)
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert f"✓ 🧪 {custom.name}" in labels
    assert f"{skye.emoji} {skye.name}" in labels
    assert "New project" in labels


async def test_catchup_on_empty_conversation_does_not_call_the_model() -> None:
    app = object.__new__(TelegramApp)
    app.access = SimpleNamespace(allowed=AsyncMock(return_value=True))
    app.rich = SimpleNamespace(send=AsyncMock())
    app.conversations = SimpleNamespace(has_items=AsyncMock(return_value=False))
    app.runtime = SimpleNamespace(run=AsyncMock())
    app.telegram_projects = SimpleNamespace(
        active=AsyncMock(
            return_value=TelegramProject(
                id="skye",
                user_id=1,
                kind="skye",
                name="Skye",
                emoji=DEFAULT_EMOJI,
                instructions="",
                openai_conversation_id=None,
                created_at="",
                updated_at="",
            )
        )
    )
    app.database = SimpleNamespace(conversation_id=AsyncMock(return_value=None))

    await app.catchup(private_message("/catchup"), AsyncMock())

    cast(AsyncMock, app.runtime.run).assert_not_called()
    app.rich.send.assert_awaited()
    assert CATCHUP_PROMPT.startswith("Catch me up")
