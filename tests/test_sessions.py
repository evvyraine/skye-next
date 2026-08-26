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


async def test_session_replay_keeps_turns_when_images_are_inline(database: Database) -> None:
    session = DatabaseSession(database, "telegram:1:0", max_chars=400)
    items = cast(
        list[TResponseInputItem],
        [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "old photo"},
                    {
                        "type": "input_image",
                        "detail": "auto",
                        "image_url": "data:image/jpeg;base64," + ("A" * 20_000),
                    },
                ],
            },
            {"role": "assistant", "content": "recent"},
            {"role": "user", "content": "latest"},
        ],
    )

    await session.add_items(items)

    assert await session.get_items() == items


async def test_session_attachment_ids_are_deduplicated_and_cascade(database: Database) -> None:
    await database.add_session_files("web-project:p1", ["or_file_1", "or_file_2"])
    await database.add_session_files("web-project:p1", ["or_file_1"])

    assert set(await database.session_files("web-project:p1")) == {
        "or_file_1",
        "or_file_2",
    }

    await database.clear_session("web-project:p1")

    assert await database.session_files("web-project:p1") == ()


async def test_session_can_rollback_items_added_after_a_checkpoint(database: Database) -> None:
    session = DatabaseSession(database, "telegram:1:0", max_chars=10_000)
    original = cast(list[TResponseInputItem], [{"role": "user", "content": "keep"}])
    partial = cast(
        list[TResponseInputItem],
        [
            {"role": "user", "content": "failed turn"},
            {"role": "assistant", "content": "partial"},
        ],
    )
    await session.add_items(original)
    checkpoint = await database.session_item_count(session.session_id)
    await session.add_items(partial)

    await database.truncate_session(session.session_id, checkpoint)

    assert await database.session_items(session.session_id) == original


async def test_session_can_replace_a_partial_tail_atomically(database: Database) -> None:
    session = DatabaseSession(database, "telegram:1:0", max_chars=10_000)
    original = cast(list[TResponseInputItem], [{"role": "user", "content": "keep"}])
    partial = cast(
        list[TResponseInputItem],
        [
            {"role": "user", "content": "new request"},
            {"type": "function_call", "name": "send_message", "call_id": "call_1"},
        ],
    )
    await session.add_items(original)
    checkpoint = await database.session_item_count(session.session_id)
    await session.add_items(partial)

    await database.replace_session_tail(
        session.session_id,
        checkpoint,
        [
            {"role": "user", "content": "new request"},
            {"role": "assistant", "content": "Delivered once."},
        ],
    )

    assert await database.session_items(session.session_id) == [
        *original,
        {"role": "user", "content": "new request"},
        {"role": "assistant", "content": "Delivered once."},
    ]
