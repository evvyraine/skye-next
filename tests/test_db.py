import json
import sqlite3
from pathlib import Path

import pytest

from skye.access import AccessService
from skye.db import Database
from skye.models import AccessEntry, GroupMessage, RequestContext, Scope


@pytest.fixture
async def database(tmp_path: Path):
    value = Database(tmp_path / "skye.db", "gpt-5.6-luna", "medium")
    await value.open()
    try:
        yield value
    finally:
        await value.close()


async def test_settings_are_scoped(database: Database) -> None:
    user = Scope("user", 42)
    group = Scope("chat", -100)

    await database.set_model(user, "gpt-5.6-sol")
    await database.set_reasoning(group, "low")

    assert (await database.get_settings(user)).model == "gpt-5.6-sol"
    assert (await database.get_settings(user)).reasoning == "medium"
    assert (await database.get_settings(group)).model == "gpt-5.6-luna"
    assert (await database.get_settings(group)).reasoning == "low"


async def test_group_access_does_not_grant_private_access(database: Database) -> None:
    access = AccessService(database, frozenset({1}))
    await database.set_access(Scope("chat", -100), "allow", created_by=1)

    group = RequestContext(-100, "supergroup", user_id=42)
    private = RequestContext(42, "private", user_id=42)

    assert await access.allowed(group)
    assert not await access.allowed(private)


async def test_list_access_returns_typed_entries(database: Database) -> None:
    await database.set_access(Scope("user", 42), "allow", created_by=1)
    await database.set_access(Scope("chat", -100), "ban", created_by=1)

    entries = await database.list_access()

    assert entries == [
        AccessEntry(Scope("user", 42), "allow", 1, entries[0].created_at),
        AccessEntry(Scope("chat", -100), "ban", 1, entries[1].created_at),
    ]


async def test_user_ban_wins_inside_allowed_group(database: Database) -> None:
    access = AccessService(database, frozenset({1}))
    await database.set_access(Scope("chat", -100), "allow", created_by=1)
    await database.set_access(Scope("user", 42), "ban", created_by=1)

    assert not await access.allowed(RequestContext(-100, "supergroup", user_id=42))
    assert await access.allowed(RequestContext(-100, "supergroup", user_id=1))


async def test_updates_are_idempotent_and_retryable(database: Database) -> None:
    assert await database.claim_update(10, '{"update_id":10}')
    assert not await database.claim_update(10, '{"update_id":10}')

    await database.finish_update(10, "TimeoutError")
    assert await database.claim_update(10, '{"update_id":10}')

    await database.finish_update(10)
    assert not await database.claim_update(10, '{"update_id":10}')


async def test_reply_thread_migration_preserves_real_topics(database: Database) -> None:
    fake_thread = GroupMessage(
        -100,
        10,
        11,
        1,
        "Alice",
        "alice",
        "Reply in the main chat",
        None,
        None,
        10,
        "Skye",
        "skye_bot",
        "Previous answer",
        1,
    )
    real_topic = GroupMessage(
        -100,
        20,
        21,
        1,
        "Alice",
        "alice",
        "Reply in a forum topic",
        None,
        None,
        20,
        "Skye",
        "skye_bot",
        "Topic answer",
        1,
    )
    await database.save_group_message(fake_thread)
    await database.save_group_message(real_topic)
    await database.claim_update(
        11,
        json.dumps(
            {
                "update_id": 11,
                "message": {
                    "message_id": 11,
                    "chat": {"id": -100, "type": "supergroup"},
                    "message_thread_id": 10,
                },
            }
        ),
    )
    await database.claim_update(
        21,
        json.dumps(
            {
                "update_id": 21,
                "message": {
                    "message_id": 21,
                    "chat": {"id": -100, "type": "supergroup"},
                    "message_thread_id": 20,
                    "is_topic_message": True,
                },
            }
        ),
    )

    await database.close()
    await database.open()

    normalized = await database.group_messages(-100, 0, before=12, limit=10)
    topic_messages = await database.group_messages(-100, 20, before=22, limit=10)

    assert [message.message_id for message in normalized] == [fake_thread.message_id]
    assert [message.message_id for message in topic_messages] == [real_topic.message_id]


async def test_memory_search_and_deletion_are_scoped(database: Database) -> None:
    private = Scope("user", 42)
    group = Scope("chat", -100)
    saved = await database.remember(private, "Prefers dark mode", "preference")
    await database.remember(group, "Project codename is Aurora", "project")

    assert [memory.id for memory in await database.search_memories(private, "dark mode")] == [
        saved.id
    ]
    assert await database.search_memories(group, "dark mode") == []
    assert not await database.forget_memory(group, saved.id)
    assert await database.forget_memory(private, saved.id)


async def test_memory_setting_is_scoped(database: Database) -> None:
    private = Scope("user", 42)
    group = Scope("chat", -100)

    await database.set_memory_enabled(private, False)
    await database.set_model(private, "gpt-5.6-sol")
    await database.set_reasoning(private, "high")

    assert not (await database.get_settings(private)).memory_enabled
    assert (await database.get_settings(group)).memory_enabled


async def test_existing_settings_tables_are_migrated(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE user_settings (
            user_id INTEGER PRIMARY KEY,
            model TEXT NOT NULL,
            reasoning TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE chat_settings (
            chat_id INTEGER PRIMARY KEY,
            model TEXT NOT NULL,
            reasoning TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE conversations (
            chat_id INTEGER NOT NULL,
            thread_id INTEGER NOT NULL DEFAULT 0,
            openai_conversation_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chat_id, thread_id)
        );
        INSERT INTO user_settings (user_id, model, reasoning)
        VALUES (42, 'gpt-5.6-luna', 'medium');
        """
    )
    connection.close()

    database = Database(path, "gpt-5.6-luna", "medium")
    await database.open()
    try:
        assert (await database.get_settings(Scope("user", 42))).memory_enabled
        assert await database.conversation_id(-100, 0) is None
    finally:
        await database.close()
