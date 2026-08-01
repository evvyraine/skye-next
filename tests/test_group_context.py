from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from aiogram.types import Chat, Message, PhotoSize, RichBlockParagraph, RichMessage, User

from skye.config import Settings
from skye.db import Database
from skye.group_context import GroupContextService
from skye.models import Scope


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

    repeated_history = await service.history(message(13, bob, "Anything new?"))
    assert "Launch is Friday" in repeated_history.transcript
    assert "Looks good" in repeated_history.transcript


async def test_history_always_contains_latest_200_messages(group_context: Any) -> None:
    _, service = group_context
    alice = User(id=1, is_bot=False, first_name="Alice", username="alice")

    for message_id in range(1, 203):
        await service.capture(message(message_id, alice, f"Message {message_id}"))

    history = await service.history(message(202, alice, "Skye, summarize"))
    lines = history.transcript.splitlines()

    assert len(lines) == 200
    assert "#2 " in lines[0]
    assert "Message 2" in lines[0]
    assert "#201 " in lines[-1]
    assert "Message 201" in lines[-1]


async def test_rich_message_text_is_kept_in_reply_context(group_context: Any) -> None:
    _, service = group_context
    alice = User(id=1, is_bot=False, first_name="Alice", username="alice")
    skye = User(id=2, is_bot=True, first_name="Skye", username="skye_bot")
    previous = Message(
        message_id=20,
        date=datetime.now(UTC),
        chat=Chat(id=-100, type="supergroup", title="Skye Lab"),
        from_user=skye,
        rich_message=RichMessage(blocks=[RichBlockParagraph(text="The launch plan is ready.")]),
    )

    await service.capture(message(21, alice, "What about timing?", reply=previous))
    history = await service.history(message(22, alice, "Skye, recap"))

    assert "replying to Skye (@skye_bot) #20 “The launch plan is ready.”" in history.transcript
    assert "[service message]" not in history.transcript


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

    assert await database.group_messages(-100, 0, before=100, limit=200) == []
