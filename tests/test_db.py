import sqlite3
from pathlib import Path

import pytest

from skye.access import AccessService
from skye.db import Database
from skye.models import RequestContext, Scope


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
        INSERT INTO user_settings (user_id, model, reasoning)
        VALUES (42, 'gpt-5.6-luna', 'medium');
        """
    )
    connection.close()

    database = Database(path, "gpt-5.6-luna", "medium")
    await database.open()
    try:
        assert (await database.get_settings(Scope("user", 42))).memory_enabled
    finally:
        await database.close()
