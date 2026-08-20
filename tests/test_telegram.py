import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import (
    Chat,
    InlineKeyboardMarkup,
    InputRichBlockTable,
    InputRichMessage,
    LinkPreviewOptions,
    Message,
    PhotoSize,
    Update,
    User,
    VideoNote,
)

from skye.artifacts import GeneratedFile
from skye.group_context import GroupHistory
from skye.models import AccessEntry, Scope
from skye.rich import RichMessages
from skye.runtime import RunOutput
from skye.telegram import AdminPrompt, TelegramApp, dump_update


def group_message(
    text: str,
    reply: Message | None = None,
    *,
    message_thread_id: int | None = None,
    is_topic_message: bool | None = None,
    photo: bool = False,
) -> Message:
    return Message(
        message_id=1,
        date=0,
        chat=Chat(id=-100, type="supergroup", title="Skye Lab"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text=None if photo else text,
        caption=text if photo else None,
        photo=(
            [PhotoSize(file_id="photo-1", file_unique_id="unique-photo", width=10, height=10)]
            if photo
            else None
        ),
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


def test_update_dump_survives_link_preview_defaults() -> None:
    message = Message(
        message_id=1,
        date=datetime(2026, 8, 15, 16, 0, 0),
        chat=Chat(id=7, type="private"),
        from_user=User(id=7, is_bot=False, first_name="Alice"),
        text="https://example.com/mcp",
        link_preview_options=LinkPreviewOptions(),
    )
    payload = dump_update(Update(update_id=1, message=message))
    parsed = Update.model_validate(json.loads(payload))

    assert parsed.update_id == 1
    assert parsed.message is not None
    assert parsed.message.text == "https://example.com/mcp"


def test_message_chunks_preserve_content() -> None:
    text = ("word " * 1000).strip()
    chunks = TelegramApp._chunks(text, limit=100)

    assert all(len(chunk) <= 100 for chunk in chunks)
    assert " ".join(chunks) == text


def test_empty_message_has_no_chunks() -> None:
    assert TelegramApp._chunks("  ") == []


def test_help_keyboard_opens_product_links() -> None:
    from skye.telegram import DOCS_URL, PRIVACY_URL, WEBSITE_URL

    markup = TelegramApp._help_keyboard()
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert [(button.text, button.url) for button in buttons] == [
        ("Website", WEBSITE_URL),
        ("Docs", DOCS_URL),
        ("Privacy policy", PRIVACY_URL),
    ]


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
        history=AsyncMock(return_value=GroupHistory('[{"message_id":1,"text":"First message"}]')),
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
    assert (
        '<recent_group_context format="json" trust="untrusted">\n'
        '[{"message_id":1,"text":"First message"}]\n'
        "</recent_group_context>"
    ) in result
    assert "Replying to Skye (@skye_example_bot) [id 777] #2: Previous answer" in result


async def test_group_photo_on_the_current_message_is_attached() -> None:
    app = telegram_app()
    app.groups = SimpleNamespace(
        text=lambda item: item.caption or item.text or "",
        history=AsyncMock(return_value=GroupHistory("")),
        sender=lambda item: (item.from_user.id, item.from_user.first_name, item.from_user.username),
    )
    app.attachments = SimpleNamespace(add=AsyncMock())
    incoming = group_message("Skye, look", photo=True)
    context = app._context(incoming)
    assert context is not None

    result = await app._input(incoming, context)

    app.attachments.add.assert_awaited_once()
    assert result[0]["content"][0]["text"].endswith("Alice [id 42]: Skye, look\n</current_message>")


async def test_group_reply_to_a_photo_is_attached() -> None:
    app = telegram_app()
    app.groups = SimpleNamespace(
        text=lambda item: item.caption or item.text or "",
        history=AsyncMock(return_value=GroupHistory("")),
        sender=lambda item: (item.from_user.id, item.from_user.first_name, item.from_user.username),
    )
    app.attachments = SimpleNamespace(add=AsyncMock())
    incoming = group_message("Skye, describe this", reply=group_message("sunset", photo=True))
    context = app._context(incoming)
    assert context is not None

    await app._input(incoming, context)

    app.attachments.add.assert_awaited_once()


async def test_group_history_photos_are_not_attached() -> None:
    app = telegram_app()
    app.groups = SimpleNamespace(
        text=lambda item: item.text or "",
        history=AsyncMock(return_value=GroupHistory("#11 · Bob [photo]: Looks good")),
    )
    app.attachments = SimpleNamespace(add=AsyncMock())
    incoming = group_message("Skye, hello")
    context = app._context(incoming)
    assert context is not None

    result = await app._input(incoming, context)

    app.attachments.add.assert_not_awaited()
    assert isinstance(result, str)
    assert "[photo]" in result


async def test_group_context_block_is_omitted_when_history_is_empty() -> None:
    app = telegram_app()
    app.groups = SimpleNamespace(
        text=lambda item: item.text or "",
        history=AsyncMock(return_value=GroupHistory("")),
    )
    incoming = group_message("Skye, hello")
    context = app._context(incoming)
    assert context is not None

    result = await app._input(incoming, context)

    assert isinstance(result, str)
    assert "<recent_group_context>" not in result
    assert "<current_message>\nAlice [id 42]: Skye, hello\n</current_message>" in result


async def test_deliver_sends_container_files_after_the_text() -> None:
    app = telegram_app()
    app.rich = SimpleNamespace(
        output=RichMessages.output,
        send=AsyncMock(),
        edit=AsyncMock(),
        send_images=AsyncMock(),
        send_documents=AsyncMock(),
    )
    incoming = private_message("make a file")
    files = (GeneratedFile("notes.md", b"hi"),)

    await app._deliver(incoming, None, RunOutput("Ready.", (), files))

    app.rich.send.assert_awaited_once()
    app.rich.send_documents.assert_awaited_once_with(incoming, files)


async def test_deliver_sends_generated_images_after_the_text() -> None:
    app = telegram_app()
    app.rich = SimpleNamespace(
        output=RichMessages.output,
        send=AsyncMock(),
        edit=AsyncMock(),
        send_images=AsyncMock(),
        send_documents=AsyncMock(),
    )
    incoming = private_message("make an image")
    images = (b"png",)

    await app._deliver(incoming, None, RunOutput("Here it is.", images))

    app.rich.send.assert_awaited_once_with(incoming, InputRichMessage(markdown="Here it is."))
    app.rich.send_images.assert_awaited_once_with(incoming, images)
    app.rich.send_documents.assert_not_awaited()


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


async def test_admin_group_keyboard_does_not_list_other_entries() -> None:
    entries = [AccessEntry(Scope("user", 42), "allow", 1, "now")]
    app = admin_app(entries)

    await app.admin(owner_group_message(), AsyncMock())

    content = app.rich.send.await_args.args[1]
    labels = button_labels(app.rich.send.await_args.kwargs["reply_markup"])
    assert labels == ["Allow this group"]
    assert content.blocks
    assert all(not isinstance(block, InputRichBlockTable) for block in content.blocks)


async def test_admin_banned_group_offers_remove_and_allow() -> None:
    entries = [AccessEntry(Scope("chat", -100), "ban", 1, "now")]
    app = admin_app(entries)

    await app.admin(owner_group_message(), AsyncMock())

    labels = button_labels(app.rich.send.await_args.kwargs["reply_markup"])
    data = button_data(app.rich.send.await_args.kwargs["reply_markup"])
    assert labels == ["Remove this group", "Allow this group"]
    assert data == ["admin:remove_group", "admin:allow_group"]


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


def test_admin_scope_from_text_parses_numeric_ids() -> None:
    assert TelegramApp._admin_scope_from_text("99") == Scope("user", 99)
    assert TelegramApp._admin_scope_from_text("-100") == Scope("chat", -100)
    assert TelegramApp._admin_scope_from_text("not-an-id") is None
    assert TelegramApp._admin_scope("chat", "-100") == Scope("chat", -100)
    with pytest.raises(ValueError, match="Unknown admin target"):
        TelegramApp._admin_scope("user", "-100")


async def test_admin_callback_asks_for_an_id() -> None:
    app = admin_app()
    state = AsyncMock()
    panel = private_message("/admin")
    callback = admin_callback_query("admin:ask:allow", panel)

    await app.admin_callback(callback, state)

    state.set_state.assert_awaited_once_with(AdminPrompt.target)
    state.set_data.assert_awaited_once_with({"action": "allow", "prompt_message_id": 1})
    content = app.rich.edit.await_args.args[1]
    assert content == RichMessages.admin_prompt("allow")
    assert button_data(app.rich.edit.await_args.kwargs["reply_markup"]) == ["admin:cancel"]
    callback.answer.assert_awaited_once()


async def test_admin_prompt_allows_numeric_id_when_replying_to_prompt() -> None:
    app = admin_app()
    state = AsyncMock()
    prompt = private_message("Reply to this message with the numeric Telegram id to allow.")
    state.get_data = AsyncMock(return_value={"action": "allow", "prompt_message_id": 1})

    await app.admin_prompt(private_message("42", reply=prompt), state)

    state.clear.assert_awaited_once()
    app.database.set_access.assert_awaited_once_with(Scope("user", 42), "allow", 1)
    app.rich.edit.assert_awaited_once()
    assert app.rich.edit.await_args.args[0] is prompt


async def test_admin_prompt_ignores_messages_that_are_not_replies_to_prompt() -> None:
    app = admin_app()
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={"action": "allow", "prompt_message_id": 9})
    alice = Message(
        message_id=2,
        date=0,
        chat=Chat(id=1, type="private", first_name="Owner"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text="hello",
    )

    assert await app.admin_prompt(private_message("42"), state) is UNHANDLED
    assert await app.admin_prompt(private_message("42", reply=alice), state) is UNHANDLED
    app.database.set_access.assert_not_awaited()
    state.clear.assert_not_awaited()


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


async def test_admin_remove_group_clears_access() -> None:
    app = admin_app([AccessEntry(Scope("chat", -100), "allow", 1, "now")])
    callback = admin_callback_query("admin:remove_group", owner_group_message())

    await app.admin_callback(callback, AsyncMock())

    app.database.remove_access.assert_awaited_once_with(Scope("chat", -100))
    app.rich.edit.assert_awaited_once()


async def test_admin_open_set_and_remove_entry() -> None:
    app = admin_app([AccessEntry(Scope("user", 42), "allow", 1, "now")])
    panel = private_message("/admin")

    await app.admin_callback(admin_callback_query("admin:open:user:42", panel), AsyncMock())
    assert button_data(app.rich.edit.await_args.kwargs["reply_markup"]) == [
        "admin:set:allow:user:42",
        "admin:set:ban:user:42",
        "admin:rm:user:42",
        "admin:home",
    ]

    await app.admin_callback(admin_callback_query("admin:set:ban:user:42", panel), AsyncMock())
    app.database.set_access.assert_awaited_once_with(Scope("user", 42), "ban", 1)

    await app.admin_callback(admin_callback_query("admin:rm:user:42", panel), AsyncMock())
    app.database.remove_access.assert_awaited_once_with(Scope("user", 42))


async def test_admin_group_rejects_allowlist_management() -> None:
    app = admin_app([AccessEntry(Scope("user", 42), "allow", 1, "now")])
    panel = owner_group_message()

    ask = admin_callback_query("admin:ask:allow", panel)
    await app.admin_callback(ask, AsyncMock())
    ask.answer.assert_awaited_once_with(
        "Manage the full allowlist in a private chat.", show_alert=True
    )

    open_entry = admin_callback_query("admin:open:user:42", panel)
    await app.admin_callback(open_entry, AsyncMock())
    open_entry.answer.assert_awaited_once_with(
        "Manage the full allowlist in a private chat.", show_alert=True
    )
    app.database.set_access.assert_not_awaited()


async def test_admin_callback_rejects_non_owner() -> None:
    app = admin_app()
    callback = admin_callback_query("admin:ask:allow", private_message("/admin"), user_id=42)

    await app.admin_callback(callback, AsyncMock())

    callback.answer.assert_awaited_once_with(
        "This is only available to the bot owner.", show_alert=True
    )
    app.rich.edit.assert_not_awaited()
