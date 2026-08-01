from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiogram.types import Chat, Document, Message, Voice
from openai import AsyncOpenAI

from skye.attachments import AttachmentService
from skye.config import Settings


class FakeBot:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    async def download(self, media: Any, destination: BytesIO) -> None:
        destination.write(self.files[media.file_id])


class Transcriptions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(text="Hello from the voice note.")


def settings(limit: int = 1024) -> Settings:
    return Settings.model_construct(
        skye_max_attachment_bytes=limit,
        skye_transcription_model="gpt-transcribe",
    )


def message(**media: Any) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=1, type="private"),
        **media,
    )


@pytest.mark.asyncio
async def test_transcribes_direct_voice() -> None:
    voice = Voice(file_id="voice", file_unique_id="unique-voice", duration=3, file_size=5)
    transcriptions = Transcriptions()
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))
    service = AttachmentService(
        settings(), cast(Any, FakeBot({"voice": b"audio"})), cast(AsyncOpenAI, client)
    )
    content: list[dict[str, Any]] = []

    await service.add(message(voice=voice), content)

    assert content == [
        {
            "type": "input_text",
            "text": "Attached audio transcript (voice.ogg):\nHello from the voice note.",
        }
    ]
    assert transcriptions.calls[0]["model"] == "gpt-transcribe"
    assert transcriptions.calls[0]["file"] == ("voice.ogg", b"audio")


@pytest.mark.asyncio
async def test_adds_replied_pdf_as_visual_file_input() -> None:
    document = Document(
        file_id="pdf",
        file_unique_id="unique-pdf",
        file_name="design.pdf",
        mime_type="application/pdf",
        file_size=4,
    )
    target = message(reply_to_message=message(document=document))
    service = AttachmentService(
        settings(),
        cast(Any, FakeBot({"pdf": b"%PDF"})),
        cast(AsyncOpenAI, SimpleNamespace()),
    )
    content: list[dict[str, Any]] = []

    await service.add(target, content)

    assert content[0] == {"type": "input_text", "text": "Replied-to document (design.pdf):"}
    assert content[1] == {
        "type": "input_file",
        "filename": "design.pdf",
        "file_data": "data:application/pdf;base64,JVBERg==",
        "detail": "auto",
    }


@pytest.mark.asyncio
async def test_rejects_attachment_larger_than_limit() -> None:
    document = Document(
        file_id="large",
        file_unique_id="unique-large",
        file_name="notes.md",
        mime_type="text/markdown",
        file_size=11,
    )
    service = AttachmentService(
        settings(10), cast(Any, FakeBot({"large": b""})), cast(AsyncOpenAI, SimpleNamespace())
    )

    with pytest.raises(ValueError, match="document is too large"):
        await service.add(message(document=document), [])
