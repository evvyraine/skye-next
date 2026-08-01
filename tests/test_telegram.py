from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User, VideoNote

from skye.telegram import TelegramApp


def group_message(text: str, reply: Message | None = None) -> Message:
    return Message(
        message_id=1,
        date=0,
        chat=Chat(id=-100, type="supergroup", title="Skye Lab"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text=text,
        reply_to_message=reply,
    )


def telegram_app() -> TelegramApp:
    app = object.__new__(TelegramApp)
    app.bot = AsyncMock()
    app.bot.id = 777
    app.bot.me.return_value = User(
        id=777, is_bot=True, first_name="Skye", username="skye_example_bot"
    )
    return app


def test_message_chunks_preserve_content() -> None:
    text = ("word " * 1000).strip()
    chunks = TelegramApp._chunks(text, limit=100)

    assert all(len(chunk) <= 100 for chunk in chunks)
    assert " ".join(chunks) == text


def test_empty_message_has_no_chunks() -> None:
    assert TelegramApp._chunks("  ") == []


@pytest.mark.parametrize(
    "text",
    [
        "Skye, help me",
        "hey skye!",
        "Скай, помоги",
        "эй, скай!",
        "@skye_example_bot help",
    ],
)
async def test_group_message_can_address_bot_by_name(text: str) -> None:
    assert await telegram_app()._directed_at_bot(group_message(text))


@pytest.mark.parametrize("text", ["ordinary group message", "skype", "скайп", "landscape"])
async def test_group_message_without_bot_address_is_ignored(text: str) -> None:
    assert not await telegram_app()._directed_at_bot(group_message(text))


async def test_undirected_group_message_does_not_check_access() -> None:
    app = telegram_app()
    app._directed_at_bot = AsyncMock(return_value=False)  # type: ignore[method-assign]
    app._require_access = AsyncMock(return_value=False)  # type: ignore[method-assign]

    await app.chat(group_message("ordinary group message"))

    app._require_access.assert_not_awaited()


async def test_private_video_note_is_processed_as_attachment() -> None:
    app = telegram_app()
    app.groups = SimpleNamespace(text=lambda _: "[video note]")
    app.attachments = SimpleNamespace(add=AsyncMock())
    incoming = Message(
        message_id=2,
        date=0,
        chat=Chat(id=42, type="private", first_name="Alice"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        video_note=VideoNote(
            file_id="video-note",
            file_unique_id="unique-video-note",
            length=240,
            duration=12,
            file_size=11,
        ),
    )
    context = app._context(incoming)
    assert context is not None

    result = await app._input(incoming, context)

    app.attachments.add.assert_awaited_once()
    assert result == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "[video note]"}],
        }
    ]
