from pathlib import Path
from typing import cast

import pytest
from agents.items import TResponseInputItem

from skye.db import Database
from skye.sessions import DatabaseSession


@pytest.fixture
async def database(tmp_path: Path):
    value = Database(tmp_path / "skye.db", "gpt-5.6-luna", "medium")
    await value.open()
    try:
        yield value
    finally:
        await value.close()


async def test_database_session_keeps_full_ledger_but_bounds_replay(database: Database) -> None:
    session = DatabaseSession(database, "telegram:-100:7", max_chars=90)
    items = cast(
        list[TResponseInputItem],
        [
            {"role": "user", "content": "old " * 30},
            {"role": "assistant", "content": "recent"},
            {"role": "user", "content": "latest"},
        ],
    )

    await session.add_items(items)

    assert len(await database.session_items(session.session_id)) == 3
    assert await session.get_items() == items[-2:]
    assert await database.session_has_items(session.session_id)

    await session.clear_session()

    assert not await database.session_has_items(session.session_id)


async def test_session_attachment_ids_are_deduplicated_and_cascade(database: Database) -> None:
    await database.add_session_files("web-project:p1", ["or_file_1", "or_file_2"])
    await database.add_session_files("web-project:p1", ["or_file_1"])

    assert set(await database.session_files("web-project:p1")) == {
        "or_file_1",
        "or_file_2",
    }

    await database.clear_session("web-project:p1")

    assert await database.session_files("web-project:p1") == ()
