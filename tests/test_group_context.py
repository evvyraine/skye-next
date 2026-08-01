from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from aiogram.types import Chat, Message, PhotoSize, User

from skye.config import Settings
from skye.db import Database
from skye.group_context import GroupContextService
from skye.models import RequestContext, Scope


def config() -> Settings:
    return Settings(
        telegram_bot_token="123:token",
        openai_api_key="sk-test",
        skye_owner_ids="1",
        _env_file=None,
    )  # type: ignore[call-arg]


def message(
    message_id: int,
    user: User,
    text: str | None = None,
    *,
    reply: Message | None = None,
    photo: bool = False,
) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=-100, type="supergroup", title="Skye Lab"),
        from_user=user,
        text=text,
        caption=text if photo else None,
        photo=(
            [PhotoSize(file_id=f"photo-{message_id}", file_unique_id="unique", width=10, height=10)]
            if photo
            else None
        ),
        reply_to_message=reply,
    )


@pytest.fixture
async def group_context(tmp_path: Path):
    database = Database(tmp_path / "groups.db", "gpt-5.6-luna", "medium")
    await database.open()
    await database.set_access(Scope("chat", -100), "allow", created_by=1)

    class BotStub:
        async def download(self, file_id: str, destination: Any) -> None:
            destination.write(file_id.encode())

    service = GroupContextService(config(), database, cast(Any, BotStub()))
    try:
        yield database, service
    finally:
        await database.close()


async def test_passive_history_keeps_people_replies_and_images(group_context: Any) -> None:
    database, service = group_context
    alice = User(id=1, is_bot=False, first_name="Alice", username="alice")
    bob = User(id=2, is_bot=False, first_name="Bob", username="bob")
    first = message(10, alice, "Launch is Friday")
    second = message(11, bob, "Looks good", reply=first, photo=True)
    current = message(12, alice, "Skye, summarize")

    await service.capture(first)
    await service.capture(second)
    history = await service.history(current)

    assert "Alice (@alice)" in history.transcript
    assert "Bob (@bob) [id 2] replying to Alice (@alice) #10" in history.transcript
    assert history.images[0][0] == 11
    assert history.images[0][1].startswith("data:image/jpeg;base64,")

    await database.save_conversation(-100, 0, "conv")
    await service.advance(RequestContext(-100, "supergroup", 1), current.message_id)
    assert await service.history(message(13, bob, "Anything new?")) == type(history)("", ())


async def test_private_messages_are_never_added_to_group_history(group_context: Any) -> None:
    database, service = group_context
    private = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=1, type="private", first_name="Alice"),
        from_user=User(id=1, is_bot=False, first_name="Alice"),
        text="Private secret",
    )

    await service.capture(private)

    assert await database.group_messages(-100, 0, after=0, before=100, limit=200) == []
