from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiogram.types import Chat, Document, Message, Video, VideoNote, Voice
from openai import AsyncOpenAI

from skye.attachments import AttachmentService
from skye.config import Settings


class FakeBot:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.downloads: list[str] = []

    async def download(self, media: Any, destination: BytesIO) -> None:
        self.downloads.append(media.file_id)
        destination.write(self.files[media.file_id])


def video(**overrides: Any) -> Video:
    payload: dict[str, Any] = {
        "file_id": "video",
        "file_unique_id": "unique-video",
        "width": 1280,
        "height": 720,
        "duration": 8,
        "file_name": "clip.mp4",
        "mime_type": "video/mp4",
        "file_size": 20,
    }
    payload.update(overrides)
    return Video(**payload)


class Transcriptions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(text="Hello from the voice note.")


class FileUploads:
    async def create(self, **_kwargs: Any) -> Any:
        return SimpleNamespace(id="file_voice")


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
async def test_replied_voice_is_transcript_input_and_keeps_file_id() -> None:
    voice = Voice(file_id="voice", file_unique_id="unique-voice", duration=3, file_size=5)
    transcriptions = Transcriptions()
    client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=transcriptions), files=FileUploads()
    )
    service = AttachmentService(
        settings(), cast(Any, FakeBot({"voice": b"audio"})), cast(AsyncOpenAI, client)
    )
    content: list[dict[str, Any]] = []

    file_ids = await service.add(message(reply_to_message=message(voice=voice)), content)

    assert file_ids == ("file_voice",)
    assert content == [
        {
            "type": "input_text",
            "text": "Replied-to audio transcript (voice.ogg):\nHello from the voice note.",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [False, True])
async def test_transcribes_video_note(reply: bool) -> None:
    video_note = VideoNote(
        file_id="video-note",
        file_unique_id="unique-video-note",
        length=240,
        duration=12,
        file_size=11,
    )
    transcriptions = Transcriptions()
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))
    service = AttachmentService(
        settings(),
        cast(Any, FakeBot({"video-note": b"video-audio"})),
        cast(AsyncOpenAI, client),
    )
    content: list[dict[str, Any]] = []
    target = (
        message(reply_to_message=message(video_note=video_note))
        if reply
        else message(video_note=video_note)
    )

    await service.add(target, content)

    label = "Replied-to" if reply else "Attached"
    assert content == [
        {
            "type": "input_text",
            "text": (
                f"{label} video message transcript (video-note.mp4):\nHello from the voice note."
            ),
        }
    ]
    assert transcriptions.calls == [
        {
            "model": "gpt-transcribe",
            "file": ("video-note.mp4", b"video-audio"),
            "response_format": "json",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [False, True])
async def test_video_becomes_text_placeholder(reply: bool) -> None:
    bot = FakeBot({})
    client = SimpleNamespace(files=FileUploads())
    service = AttachmentService(settings(), cast(Any, bot), cast(AsyncOpenAI, client))
    content: list[dict[str, Any]] = []
    clip = video()
    target = (
        message(reply_to_message=message(video=clip, caption="Channel recap"))
        if reply
        else message(video=clip, caption="Channel recap")
    )

    file_ids = await service.add(target, content)

    assert file_ids == ()
    assert bot.downloads == []
    label = "Replied-to" if reply else "Attached"
    expected = f"{label} video (clip.mp4): the model cannot view this video."
    if reply:
        expected += "\nCaption:\nChannel recap"
    assert content == [{"type": "input_text", "text": expected}]


@pytest.mark.asyncio
async def test_video_placeholder_skips_download_even_when_over_size_limit() -> None:
    bot = FakeBot({})
    service = AttachmentService(
        settings(10),
        cast(Any, bot),
        cast(AsyncOpenAI, SimpleNamespace(files=FileUploads())),
    )
    content: list[dict[str, Any]] = []

    file_ids = await service.add(message(video=video(file_size=11)), content)

    assert file_ids == ()
    assert bot.downloads == []
    assert content == [
        {
            "type": "input_text",
            "text": "Attached video (clip.mp4): the model cannot view this video.",
        }
    ]


@pytest.mark.asyncio
async def test_video_sent_as_document_becomes_placeholder_without_upload() -> None:
    document = Document(
        file_id="animation",
        file_unique_id="unique-animation",
        file_name="clip.mp4",
        mime_type="video/mp4",
        file_size=20,
    )
    bot = FakeBot({})
    service = AttachmentService(settings(), cast(Any, bot), cast(AsyncOpenAI, SimpleNamespace()))
    content: list[dict[str, Any]] = []

    file_ids = await service.add(message(document=document), content)

    assert file_ids == ()
    assert bot.downloads == []
    assert content == [
        {
            "type": "input_text",
            "text": "Attached video (clip.mp4): the model cannot view this video.",
        }
    ]


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
