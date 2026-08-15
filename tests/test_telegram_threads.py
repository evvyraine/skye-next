from aiogram.types import Chat, Message

from skye.telegram_threads import api_thread_id, reply_parameters, thread_id


def message(*, raw_thread_id: int | None, is_topic: bool | None) -> Message:
    return Message(
        message_id=20,
        date=0,
        chat=Chat(id=-100, type="supergroup", title="Skye Lab"),
        message_thread_id=raw_thread_id,
        is_topic_message=is_topic,
    )


def test_reply_thread_id_is_ignored_outside_forum_topic() -> None:
    reply = message(raw_thread_id=19, is_topic=None)

    assert thread_id(reply) == 0
    assert api_thread_id(reply) is None


def test_real_forum_topic_id_is_preserved() -> None:
    topic_message = message(raw_thread_id=19, is_topic=True)

    assert thread_id(topic_message) == 19
    assert api_thread_id(topic_message) == 19


def test_reply_parameters_attach_to_the_triggering_message() -> None:
    incoming = message(raw_thread_id=None, is_topic=None)

    params = reply_parameters(incoming)

    assert params.message_id == incoming.message_id
    assert params.allow_sending_without_reply is True
