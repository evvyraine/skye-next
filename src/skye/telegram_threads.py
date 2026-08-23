from aiogram.types import Message, ReplyParameters


def thread_id(message: Message) -> int:
    """Return a stable conversation key, ignoring reply-only Telegram thread ids."""
    value = message.message_thread_id
    if value is None:
        return 0
    if message.chat.type in {"group", "supergroup"} and not message.is_topic_message:
        return 0
    return value


def api_thread_id(message: Message) -> int | None:
    return thread_id(message) or None


def reply_parameters(message: Message) -> ReplyParameters:
    """Quote-reply to a known message. Unknown ids still send."""
    return ReplyParameters(
        message_id=message.message_id,
        allow_sending_without_reply=True,
    )


def quote_reply(message_id: int | None) -> ReplyParameters | None:
    """Build quote-reply parameters, or None for a standalone bubble."""
    if message_id is None or message_id <= 0:
        return None
    return ReplyParameters(
        message_id=message_id,
        allow_sending_without_reply=True,
    )
