from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from skye.conversations import ConversationService
from skye.db import Database


@pytest.fixture
async def database(tmp_path: Path):
    value = Database(tmp_path / "skye.db", "gpt-5.6-luna", "medium")
    await value.open()
    try:
        yield value
    finally:
        await value.close()


async def test_response_chaining_uses_local_session_and_cursor(database: Database) -> None:
    client = AsyncMock()
    service = ConversationService(
        database,
        cast(Any, client),
        remote=False,
    )

    session_id = await service.get_or_create(1, 7)

    assert session_id == "telegram:1:7"
    assert not await service.has_items(session_id)
    client.conversations.create.assert_not_awaited()

    await database.add_session_items(session_id, [{"role": "user", "content": "Hello"}])
    assert await service.has_items(session_id)

    assert await service.reset(1, 7)
    assert not await database.session_has_items(session_id)
    assert await database.conversation_id(1, 7) is None
    client.conversations.delete.assert_not_awaited()
