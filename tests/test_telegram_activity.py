import asyncio
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest

from skye.telegram_activity import TelegramActivity


async def test_activity_starts_changes_and_refreshes_native_status() -> None:
    bot = AsyncMock()
    activity = TelegramActivity(bot, -100, 42, refresh_seconds=0.01)

    async with activity:
        await activity.show("upload_photo")
        await asyncio.sleep(0.02)

    actions = [call.kwargs["action"] for call in bot.send_chat_action.await_args_list]
    assert actions[:2] == ["typing", "upload_photo"]
    assert actions[-1] == "upload_photo"
    assert all(
        call.kwargs["message_thread_id"] == 42
        for call in bot.send_chat_action.await_args_list
    )


async def test_activity_is_silent_for_background_turns() -> None:
    bot = AsyncMock()

    async with TelegramActivity(bot, 7, enabled=False) as activity:
        await activity.show("upload_document")

    bot.send_chat_action.assert_not_awaited()


async def test_activity_failure_does_not_break_the_turn() -> None:
    bot = AsyncMock()
    bot.send_chat_action.side_effect = TelegramBadRequest(method="sendChatAction", message="no")

    async with TelegramActivity(bot, 7) as activity:
        await activity.show("record_voice")
