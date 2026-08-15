from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, InlineKeyboardMarkup, Message, User, VideoNote

from skye.group_context import GroupHistory
from skye.models import AccessEntry, Scope
from skye.rich import RichMessages
from skye.telegram import AdminPrompt, TelegramApp


def group_message(
    text: str,
    reply: Message | None = None,
    *,
    message_thread_id: int | None = None,
    is_topic_message: bool | None = None,
) -> Message:
    return Message(
        message_id=1,
        date=0,
        chat=Chat(id=-100, type="supergroup", title="Skye Lab"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text=text,
        reply_to_message=reply,
        message_thread_id=message_thread_id,
        is_topic_message=is_topic_message,
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


async def test_group_reply_always_includes_recent_context() -> None:
    app = telegram_app()
    app.groups = SimpleNamespace(
        text=lambda item: item.text or "",
        sender=lambda item: (item.from_user.id, item.from_user.first_name, item.from_user.username),
        history=AsyncMock(return_value=GroupHistory("#1 · Alice: First message", ())),
    )
    skye_message = Message(
        message_id=2,
        date=0,
        chat=Chat(id=-100, type="supergroup", title="Skye Lab"),
        from_user=User(id=777, is_bot=True, first_name="Skye", username="skye_example_bot"),
        text="Previous answer",
    )
    incoming = group_message(
        "Current reply", reply=skye_message, message_thread_id=skye_message.message_id
    )
    context = app._context(incoming)
    assert context is not None
    assert context.thread_id == 0

    result = await app._input(incoming, context)

    assert isinstance(result, str)
    assert "<recent_group_context>\n#1 · Alice: First message\n</recent_group_context>" in result
    assert "Replying to Skye (@skye_example_bot) [id 777] #2: Previous answer" in result


async def test_group_context_block_is_present_when_history_is_empty() -> None:
    app = telegram_app()
    app.groups = SimpleNamespace(
        text=lambda item: item.text or "",
        history=AsyncMock(return_value=GroupHistory("", ())),
    )
    incoming = group_message("Skye, hello")
    context = app._context(incoming)
    assert context is not None

    result = await app._input(incoming, context)

    assert isinstance(result, str)
    assert "<recent_group_context>\n\n</recent_group_context>" in result


def private_message(
    text: str,
    *,
    user_id: int = 1,
    first_name: str = "Owner",
    reply: Message | None = None,
) -> Message:
    return Message(
        message_id=1,
        date=0,
        chat=Chat(id=user_id, type="private", first_name=first_name),
        from_user=User(id=user_id, is_bot=False, first_name=first_name),
        text=text,
        reply_to_message=reply,
    )


def owner_group_message(text: str = "/admin", reply: Message | None = None) -> Message:
    return Message(
        message_id=1,
        date=0,
        chat=Chat(id=-100, type="supergroup", title="Skye Lab"),
        from_user=User(id=1, is_bot=False, first_name="Owner"),
        text=text,
        reply_to_message=reply,
    )


def admin_app(entries: list[AccessEntry] | None = None) -> TelegramApp:
    app = telegram_app()
    app.access = SimpleNamespace(is_owner=lambda user_id: user_id == 1)
    app.database = SimpleNamespace(
        list_access=AsyncMock(return_value=list(entries or [])),
        set_access=AsyncMock(),
        remove_access=AsyncMock(return_value=True),
    )
    app.rich = SimpleNamespace(send=AsyncMock(), edit=AsyncMock(), access=RichMessages.access)
    return app


def admin_callback_query(data: str, message: Message, user_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        message=message,
        data=data,
        from_user=User(id=user_id, is_bot=False, first_name="Owner"),
        answer=AsyncMock(),
    )


def button_labels(markup: InlineKeyboardMarkup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def button_data(markup: InlineKeyboardMarkup) -> list[str]:
    return [button.callback_data or "" for row in markup.inline_keyboard for button in row]


async def test_admin_rejects_non_owner() -> None:
    app = admin_app()
    app.access = SimpleNamespace(is_owner=lambda _: False)

    await app.admin(private_message("/admin", user_id=42, first_name="Alice"), AsyncMock())

    app.rich.send.assert_awaited_once()
    assert app.rich.send.await_args.args[1] == "This command is only available to the bot owner."
    app.database.list_access.assert_not_awaited()


async def test_admin_opens_keyboard_and_ignores_subcommands() -> None:
    app = admin_app()
    state = AsyncMock()

    await app.admin(private_message("/admin allow 99"), state)

    state.clear.assert_awaited_once()
    app.database.set_access.assert_not_awaited()
    markup = app.rich.send.await_args.kwargs["reply_markup"]
    labels = button_labels(markup)
    assert labels == ["Allow", "Ban", "Remove"]
    assert button_data(markup) == ["admin:ask:allow", "admin:ask:ban", "admin:ask:remove"]


async def test_admin_group_keyboard_can_allow_this_group() -> None:
    app = admin_app()

    await app.admin(owner_group_message(), AsyncMock())

    labels = button_labels(app.rich.send.await_args.kwargs["reply_markup"])
    assert labels[:4] == ["Allow this group", "Allow", "Ban", "Remove"]


async def test_admin_reply_offers_user_buttons() -> None:
    app = admin_app()
    alice = Message(
        message_id=2,
        date=0,
        chat=Chat(id=1, type="private", first_name="Owner"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text="hello",
    )

    await app.admin(private_message("/admin", reply=alice), AsyncMock())

    labels = button_labels(app.rich.send.await_args.kwargs["reply_markup"])
    data = button_data(app.rich.send.await_args.kwargs["reply_markup"])
    assert "Allow Alice" in labels
    assert "Ban Alice" in labels
    assert "admin:set:allow:user:42" in data
    assert "admin:set:ban:user:42" in data


def test_admin_input_target_ignores_bot_replies() -> None:
    bot_reply = Message(
        message_id=2,
        date=0,
        chat=Chat(id=1, type="private", first_name="Owner"),
        from_user=User(id=777, is_bot=True, first_name="Skye"),
        text="Reply with the numeric Telegram id to allow, or reply to that user.",
    )

    assert TelegramApp._admin_input_target(private_message("42", reply=bot_reply)) == Scope(
        "user", 42
    )
    assert TelegramApp._admin_input_target(private_message("not-an-id", reply=bot_reply)) is None


def test_admin_input_target_uses_replied_user_and_numeric_ids() -> None:
    alice = Message(
        message_id=2,
        date=0,
        chat=Chat(id=1, type="private", first_name="Owner"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text="hello",
    )

    assert TelegramApp._admin_input_target(private_message("ignored", reply=alice)) == Scope(
        "user", 42
    )
    assert TelegramApp._admin_input_target(private_message("99")) == Scope("user", 99)
    assert TelegramApp._admin_input_target(private_message("-100")) == Scope("chat", -100)
    assert TelegramApp._admin_scope("chat", "-100") == Scope("chat", -100)
    with pytest.raises(ValueError, match="Unknown admin target"):
        TelegramApp._admin_scope("user", "-100")


async def test_admin_callback_asks_for_an_id() -> None:
    app = admin_app()
    state = AsyncMock()
    callback = admin_callback_query("admin:ask:allow", private_message("/admin"))

    await app.admin_callback(callback, state)

    state.set_state.assert_awaited_once_with(AdminPrompt.target)
    state.set_data.assert_awaited_once_with({"action": "allow"})
    assert "Reply with the numeric Telegram id to allow" in app.rich.edit.await_args.args[1]
    assert button_data(app.rich.edit.await_args.kwargs["reply_markup"]) == ["admin:cancel"]
    callback.answer.assert_awaited_once()


async def test_admin_prompt_allows_numeric_id() -> None:
    app = admin_app()
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={"action": "allow"})

    await app.admin_prompt(private_message("42"), state)

    state.clear.assert_awaited_once()
    app.database.set_access.assert_awaited_once_with(Scope("user", 42), "allow", 1)
    assert app.rich.send.await_args.kwargs["reply_markup"] is not None


async def test_admin_prompt_allows_replied_user() -> None:
    app = admin_app()
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={"action": "ban"})
    alice = Message(
        message_id=2,
        date=0,
        chat=Chat(id=1, type="private", first_name="Owner"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text="hello",
    )

    await app.admin_prompt(private_message("ban this", reply=alice), state)

    app.database.set_access.assert_awaited_once_with(Scope("user", 42), "ban", 1)


async def test_admin_cannot_ban_owner() -> None:
    app = admin_app()

    with pytest.raises(PermissionError, match="owner cannot be banned"):
        await app._apply_admin(1, "ban", Scope("user", 1))

    app.database.set_access.assert_not_awaited()


async def test_admin_allow_group_sets_access() -> None:
    app = admin_app()
    callback = admin_callback_query("admin:allow_group", owner_group_message())

    await app.admin_callback(callback, AsyncMock())

    app.database.set_access.assert_awaited_once_with(Scope("chat", -100), "allow", 1)
    app.rich.edit.assert_awaited_once()


async def test_admin_callback_rejects_non_owner() -> None:
    app = admin_app()
    callback = admin_callback_query("admin:ask:allow", private_message("/admin"), user_id=42)

    await app.admin_callback(callback, AsyncMock())

    callback.answer.assert_awaited_once_with(
        "This is only available to the bot owner.", show_alert=True
    )
    app.rich.edit.assert_not_awaited()
