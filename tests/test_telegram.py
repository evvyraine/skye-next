from skye.telegram import TelegramApp


def test_message_chunks_preserve_content() -> None:
    text = ("word " * 1000).strip()
    chunks = TelegramApp._chunks(text, limit=100)

    assert all(len(chunk) <= 100 for chunk in chunks)
    assert " ".join(chunks) == text


def test_empty_message_has_no_chunks() -> None:
    assert TelegramApp._chunks("  ") == []
