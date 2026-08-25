from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import StrEnum
from typing import overload

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputRichBlockParagraph,
    InputRichBlockThinking,
    InputRichMessage,
    KeyboardButton,
    ReplyKeyboardMarkup,
    RichTextCustomEmoji,
    RichTextUnion,
)


class Icon(StrEnum):
    CLOSE = "5220160224299623455"
    EDIT = "5219674923059947621"
    SHIELD = "5219823516043485651"
    HELP = "5219757223223272542"
    SEARCH = "5219940425053282382"
    IDEA = "5219704816032331845"
    SUPPORT = "5219945660618418764"
    CALENDAR = "5220072001376398106"
    BRIEFCASE = "5220054018348329156"
    EMAIL = "5219807139333188475"
    REFRESH = "5217944661125013027"
    BACK = "5220063175218604987"
    PUZZLE = "5220068513862954768"
    IMAGE = "5219798910175850946"
    NEWS = "5219840214876333436"
    ANNOUNCEMENT = "5219813100747793570"
    UNLOCKED = "5220144551963962986"
    LOCKED = "5220098209266836336"
    LINK = "5219753048515059901"
    HEART = "5217726416656836923"
    LIGHTNING = "5219788619434208081"
    BOOKMARK = "5219930559513406806"
    MESSAGE = "5219731285915771391"
    SETTINGS = "5220199372926530082"
    TERMINAL = "5220135931964597504"
    FOLDER = "5217757958896657375"
    GIFT = "5217601763821004212"
    GLOBE = "5220073002103774955"
    SPARKLES = "5217552844143504410"
    APPS_ADD = "5220155736058800623"
    DELETE = "5217677909296194391"
    USER = "5219716798991084516"


@dataclass(frozen=True, slots=True)
class Activity:
    text: str
    icon: Icon
    alternative: str

    def rich_text(self) -> list[RichTextUnion]:
        return [
            RichTextCustomEmoji(
                custom_emoji_id=self.icon,
                alternative_text=self.alternative,
            ),
            f" {self.text}",
        ]


ACTIVITIES: tuple[Activity, ...] = (
    Activity("Warming up the computer…", Icon.TERMINAL, "💻"),
    Activity("Mixing colors…", Icon.IMAGE, "🖼️"),
    Activity("Connecting the dots…", Icon.LINK, "🔗"),
    Activity("Gathering thoughts…", Icon.MESSAGE, "💬"),
    Activity("Opening the notebook…", Icon.BOOKMARK, "📔"),
    Activity("Tuning the gears…", Icon.SETTINGS, "⚙️"),
    Activity("Exploring the map…", Icon.GLOBE, "🌐"),
    Activity("Assembling the pieces…", Icon.PUZZLE, "🧩"),
    Activity("Adding a little spark…", Icon.SPARKLES, "✨"),
    Activity("Sorting the files…", Icon.FOLDER, "📂"),
    Activity("Polishing the details…", Icon.EDIT, "🎨"),
    Activity("Looking for a bright idea…", Icon.IDEA, "💡"),
)


def activity_message(*, draft: bool) -> InputRichMessage:
    activity = secrets.choice(ACTIVITIES)
    block = (
        InputRichBlockThinking(text=activity.rich_text())
        if draft
        else InputRichBlockParagraph(text=activity.rich_text())
    )
    return InputRichMessage(blocks=[block])


@overload
def decorate_keyboard(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup: ...


@overload
def decorate_keyboard(markup: ReplyKeyboardMarkup) -> ReplyKeyboardMarkup: ...


@overload
def decorate_keyboard(markup: None) -> None: ...


def decorate_keyboard(
    markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None,
) -> InlineKeyboardMarkup | ReplyKeyboardMarkup | None:
    if isinstance(markup, InlineKeyboardMarkup):
        return markup.model_copy(
            update={
                "inline_keyboard": [
                    [_decorate_inline(button) for button in row] for row in markup.inline_keyboard
                ]
            }
        )
    if isinstance(markup, ReplyKeyboardMarkup):
        return markup.model_copy(
            update={
                "keyboard": [[_decorate_reply(button) for button in row] for row in markup.keyboard]
            }
        )
    return None


def _decorate_inline(button: InlineKeyboardButton) -> InlineKeyboardButton:
    icon = button.icon_custom_emoji_id or _button_icon(
        button.text,
        callback_data=button.callback_data,
        url=button.url,
    )
    return button.model_copy(update={"icon_custom_emoji_id": icon}) if icon else button


def _decorate_reply(button: KeyboardButton) -> KeyboardButton:
    icon = button.icon_custom_emoji_id or _button_icon(button.text)
    return button.model_copy(update={"icon_custom_emoji_id": icon}) if icon else button


def _button_icon(
    text: str, *, callback_data: str | None = None, url: str | None = None
) -> Icon | None:
    label = text.casefold()
    callback = callback_data or ""

    action = label.strip("‹› ")
    if action in {"back", "prev"}:
        return Icon.BACK
    if action == "cancel":
        return Icon.CLOSE
    if action in {"save", "done"}:
        return Icon.SHIELD
    if action == "search":
        return Icon.SEARCH
    if action in {"next", "skip"}:
        return None

    if callback.startswith("skill:add"):
        return Icon.APPS_ADD
    if callback.startswith(("skill:del", "skill:yes")):
        return Icon.DELETE
    if callback.startswith(("skill:", "settings:skills")):
        return Icon.PUZZLE
    if callback.startswith(("settings:adel", "settings:arm")):
        return Icon.DELETE
    if callback.startswith("settings:ahook"):
        return Icon.LINK
    if callback.startswith(("settings:auto",)):
        return Icon.ANNOUNCEMENT if "webhook" in label else Icon.CALENDAR
    if callback.startswith(("settings:memory",)):
        return Icon.BOOKMARK
    if callback.startswith(("settings:reason",)):
        return Icon.IDEA
    if callback.startswith(("settings:agents", "settings:agent", "agents:")):
        if callback.startswith("agents:add"):
            return Icon.APPS_ADD
        if callback.startswith("agents:remove"):
            return Icon.DELETE
        if callback.startswith(("agents:select", "settings:agent")):
            return Icon.UNLOCKED
        if callback.startswith("agents:edit"):
            return Icon.EDIT
        if callback.startswith("agents:share"):
            return Icon.LINK
        if callback.endswith(":web"):
            return Icon.GLOBE
        if callback.endswith(":image"):
            return Icon.IMAGE
        if callback.endswith(":shell"):
            return Icon.TERMINAL
        return Icon.SPARKLES
    if callback.startswith(("settings:projects", "proj:")):
        if callback.startswith("proj:new"):
            return Icon.APPS_ADD
        if callback.startswith(("proj:emoji", "proj:emo", "proj:icon", "proj:any")):
            return Icon.IMAGE
        if callback.startswith("proj:use"):
            return Icon.UNLOCKED
        if callback.startswith("proj:catch"):
            return Icon.MESSAGE
        if callback.startswith(("proj:reset", "proj:wipe")):
            return Icon.REFRESH
        if callback.startswith(("proj:del", "proj:yes")):
            return Icon.DELETE
        if callback.startswith(("proj:name", "proj:inst")):
            return Icon.EDIT if callback.startswith("proj:name") else Icon.NEWS
        if callback.startswith(("proj:open", "proj:page")):
            return Icon.FOLDER
        return Icon.BRIEFCASE
    if callback.startswith(("conn:", "settings:connectors")):
        if callback.startswith(("conn:name", "conn:url", "conn:hdr")):
            return Icon.EDIT
        if callback.startswith(("conn:new", "conn:open")):
            return Icon.TERMINAL
        if callback.startswith("conn:tog"):
            return Icon.LOCKED if "turn off" in label else Icon.UNLOCKED
        if callback.startswith(("conn:add", "conn:mine")):
            return Icon.APPS_ADD
        if callback.startswith(("conn:del", "conn:yes", "conn:rv")):
            return Icon.DELETE
        if callback.startswith("conn:off"):
            return Icon.LOCKED
        if callback.startswith("conn:link"):
            return Icon.REFRESH if "reconnect" in label else Icon.UNLOCKED
        if callback.startswith("conn:chk"):
            return Icon.SHIELD
        if callback.startswith(("conn:pick", "conn:ask", "conn:ok")):
            return Icon.LINK
        return Icon.GLOBE
    if callback.startswith("acct:"):
        if callback.startswith("acct:cancel"):
            return Icon.DELETE
        if callback == "acct:home":
            return Icon.HEART
        return Icon.GIFT
    if callback.startswith("admin:"):
        if ":allow" in callback or callback == "admin:allow_group":
            return Icon.UNLOCKED
        if ":ban" in callback:
            return Icon.LOCKED
        if any(part in callback for part in (":remove", ":rm")):
            return Icon.DELETE
        return Icon.USER

    if any(word in label for word in ("delete", "remove", "stop sharing")):
        return Icon.DELETE
    if "privacy" in label:
        return Icon.SHIELD
    if any(word in label for word in ("ban", "turn off", "disconnect")):
        return Icon.LOCKED
    if any(word in label for word in ("allow", "turn on", "connect", "reconnect")):
        return Icon.UNLOCKED
    if any(word in label for word in ("website", "web")):
        return Icon.GLOBE
    if any(word in label for word in ("help", "support")):
        return Icon.SUPPORT if "support" in label else Icon.HELP
    if any(word in label for word in ("docs", "instructions")):
        return Icon.NEWS
    if any(word in label for word in ("share", "url", "hook")) or url:
        return Icon.LINK
    if any(word in label for word in ("edit", "rename")):
        return Icon.EDIT
    if "settings" in label:
        return Icon.SETTINGS
    if any(word in label for word in ("project", "projects")):
        return Icon.BRIEFCASE
    if "catch up" in label:
        return Icon.MESSAGE
    if any(word in label for word in ("agent", "skye")):
        return Icon.SPARKLES
    if any(word in label for word in ("account", "user")):
        return Icon.USER
    if any(word in label for word in ("subscribe", "pay", "plan")):
        return Icon.GIFT
    if any(word in label for word in ("reset", "refresh", "reconnect")):
        return Icon.REFRESH
    if any(word in label for word in ("email", "mail")):
        return Icon.EMAIL
    return None
