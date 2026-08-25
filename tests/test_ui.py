from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputRichBlockParagraph,
    InputRichBlockThinking,
    KeyboardButton,
    ReplyKeyboardMarkup,
    RichTextCustomEmoji,
)

from skye.ui import ACTIVITIES, Icon, activity_message, decorate_keyboard


def test_inline_keyboards_receive_semantic_custom_icons() -> None:
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Skills", callback_data="settings:skills")],
            [InlineKeyboardButton(text="Delete", callback_data="proj:del:abc")],
            [InlineKeyboardButton(text="Back", callback_data="settings:back")],
        ]
    )

    decorated = decorate_keyboard(markup)

    assert decorated.inline_keyboard[0][0].icon_custom_emoji_id == Icon.PUZZLE
    assert decorated.inline_keyboard[1][0].icon_custom_emoji_id == Icon.DELETE
    assert decorated.inline_keyboard[2][0].icon_custom_emoji_id == Icon.BACK


def test_button_actions_override_their_feature_icon() -> None:
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Connect", callback_data="conn:link:gmail")],
            [InlineKeyboardButton(text="Disconnect", callback_data="conn:off:gmail")],
            [InlineKeyboardButton(text="Add agent", callback_data="agents:add")],
            [InlineKeyboardButton(text="Share", callback_data="agents:share:abc")],
        ]
    )

    decorated = decorate_keyboard(markup)
    icons = [button.icon_custom_emoji_id for row in decorated.inline_keyboard for button in row]

    assert icons == [Icon.UNLOCKED, Icon.LOCKED, Icon.APPS_ADD, Icon.LINK]


def test_reply_keyboards_receive_custom_icons_without_text_emoji() -> None:
    markup = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Projects"), KeyboardButton(text="Catch up")]]
    )

    decorated = decorate_keyboard(markup)

    assert decorated.keyboard[0][0].icon_custom_emoji_id == Icon.BRIEFCASE
    assert decorated.keyboard[0][1].icon_custom_emoji_id == Icon.MESSAGE


def test_activity_messages_use_matching_custom_emoji(monkeypatch: object) -> None:
    activity = ACTIVITIES[1]
    monkeypatch.setattr("skye.ui.secrets.choice", lambda _items: activity)  # type: ignore[attr-defined]

    draft = activity_message(draft=True)
    placeholder = activity_message(draft=False)

    assert isinstance(draft.blocks[0], InputRichBlockThinking)
    assert isinstance(placeholder.blocks[0], InputRichBlockParagraph)
    for block in (draft.blocks[0], placeholder.blocks[0]):
        assert isinstance(block.text, list)
        assert isinstance(block.text[0], RichTextCustomEmoji)
        assert block.text[0].custom_emoji_id == Icon.IMAGE
        assert block.text[1] == " Mixing colors…"


def test_new_action_icons_replace_fallbacks() -> None:
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Edit name", callback_data="conn:name:abc")],
            [InlineKeyboardButton(text="Search", callback_data="conn:search")],
            [InlineKeyboardButton(text="Reset chat", callback_data="proj:reset:abc")],
            [InlineKeyboardButton(text="Save", callback_data="conn:save")],
            [InlineKeyboardButton(text="Cancel", callback_data="conn:home")],
        ]
    )

    decorated = decorate_keyboard(markup)
    icons = [button.icon_custom_emoji_id for row in decorated.inline_keyboard for button in row]

    assert icons == [Icon.EDIT, Icon.SEARCH, Icon.REFRESH, Icon.SHIELD, Icon.CLOSE]


def test_project_and_emoji_rows_do_not_duplicate_their_own_icons() -> None:
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✓ ☁️ Skye", callback_data="proj:open:skye")],
            [InlineKeyboardButton(text="☁️", callback_data="proj:emo:0")],
            [InlineKeyboardButton(text="💬", callback_data="proj:icon:abc:1")],
            [InlineKeyboardButton(text="Send any emoji", callback_data="proj:any")],
        ]
    )

    decorated = decorate_keyboard(markup)
    icons = [button.icon_custom_emoji_id for row in decorated.inline_keyboard for button in row]

    assert icons == [None, None, None, Icon.IMAGE]


def test_back_uses_only_the_custom_icon() -> None:
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="‹ Back", callback_data="settings:back")],
            [InlineKeyboardButton(text="‹ Prev", callback_data="proj:page:0")],
        ]
    )

    decorated = decorate_keyboard(markup)
    buttons = [button for row in decorated.inline_keyboard for button in row]

    assert [(button.text, button.icon_custom_emoji_id) for button in buttons] == [
        ("Back", Icon.BACK),
        ("Prev", Icon.BACK),
    ]
