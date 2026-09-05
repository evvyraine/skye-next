from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from aiogram.types import Chat, Message, PhotoSize, User
from openai import AsyncOpenAI

from skye.attachments import AttachmentService
from skye.config import Settings
from skye.db import Database
from skye.media_groups import MediaGroupService
from skye.models import MediaGroupItem


def settings() -> Settings:
    return Settings.model_construct(
        skye_max_attachment_bytes=1024,
        skye_media_group_settle_seconds=0.1,
        skye_transcription_model="gpt-transcribe",
    )


def photo(message_id: int, group_id: str) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=42, type="private"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        media_group_id=group_id,
        photo=[
            PhotoSize(
                file_id=f"photo-{message_id}",
                file_unique_id=f"unique-{message_id}",
                width=100,
                height=100,
                file_size=4,
            )
        ],
    )


async def test_media_group_reply_resolves_every_member(tmp_path: Path) -> None:
    database = Database(tmp_path / "albums.db", "gpt-5.6-luna", "medium")
    await database.open()
    try:
        service = MediaGroupService(settings(), database)
        members = [photo(index, "album-1") for index in range(1, 4)]
        for member in members:
            await service.capture(member)

        reply = photo(2, "album-1").model_copy(update={"media_group_id": None})
        current = Message(
            message_id=10,
            date=datetime.now(UTC),
            chat=members[0].chat,
            from_user=members[0].from_user,
            text="Describe all of them",
            reply_to_message=reply,
        )

        resolved = await service.resolve(current)

        assert [item.file_id for item in resolved] == ["photo-1", "photo-2", "photo-3"]
        assert await service.claim(members[0])
        assert not await service.claim(members[1])
    finally:
        await database.close()


class DownloadBot:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    async def download(self, media: Any, destination: BytesIO) -> None:
        destination.write(self.files[media.file_id])


def item(index: int, kind: str = "photo") -> MediaGroupItem:
    return MediaGroupItem(
        chat_id=42,
        media_group_id="album-1",
        message_id=index,
        thread_id=0,
        media_kind=kind,
        file_id=f"file-{index}",
        file_unique_id=f"unique-{index}",
        file_name=f"file-{index}.pdf" if kind == "document" else None,
        mime_type="application/pdf" if kind == "document" else None,
        file_size=4,
        width=100 if kind == "photo" else None,
        height=100 if kind == "photo" else None,
        caption=None,
        sent_at=1,
    )


def text_message() -> Message:
    return Message(
        message_id=99,
        date=datetime.now(UTC),
        chat=Chat(id=42, type="private"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text="Use these files",
    )


async def test_album_photos_become_multiple_vision_inputs() -> None:
    service = AttachmentService(
        settings(),
        cast(Any, DownloadBot({"file-1": b"one", "file-2": b"two", "file-3": b"three"})),
        cast(AsyncOpenAI, SimpleNamespace()),
    )
    content: list[dict[str, Any]] = []

    await service.add(text_message(), content, album=[item(1), item(2), item(3)])

    assert [part["type"] for part in content if part["type"] == "input_image"] == [
        "input_image",
        "input_image",
        "input_image",
    ]


async def test_album_photos_use_image_data_urls() -> None:
    service = AttachmentService(
        settings(),
        cast(Any, DownloadBot({"file-1": b"one", "file-2": b"two"})),
        cast(AsyncOpenAI, SimpleNamespace()),
    )
    content: list[dict[str, Any]] = []

    file_ids = await service.add(text_message(), content, album=[item(1), item(2)])

    images = [part for part in content if part["type"] == "input_image"]
    assert [part.get("file_id") for part in images] == [None, None]
    assert [part["image_url"] for part in images] == [
        "data:image/jpeg;base64,b25l",
        "data:image/jpeg;base64,dHdv",
    ]
    assert file_ids == ()


async def test_album_documents_become_multiple_file_inputs() -> None:
    service = AttachmentService(
        settings(),
        cast(Any, DownloadBot({"file-1": b"one", "file-2": b"two"})),
        cast(AsyncOpenAI, SimpleNamespace()),
    )
    content: list[dict[str, Any]] = []

    await service.add(text_message(), content, album=[item(1, "document"), item(2, "document")])

    assert [part["filename"] for part in content if part["type"] == "input_file"] == [
        "file-1.pdf",
        "file-2.pdf",
    ]


async def test_album_documents_use_inline_file_data() -> None:
    service = AttachmentService(
        settings(),
        cast(Any, DownloadBot({"file-1": b"one", "file-2": b"two"})),
        cast(AsyncOpenAI, SimpleNamespace()),
    )
    content: list[dict[str, Any]] = []

    file_ids = await service.add(
        text_message(), content, album=[item(1, "document"), item(2, "document")]
    )

    assert file_ids == ()
    assert [part["filename"] for part in content if part["type"] == "input_file"] == [
        "file-1.pdf",
        "file-2.pdf",
    ]
    assert all("file_data" in part for part in content if part["type"] == "input_file")


async def test_album_videos_become_text_placeholders() -> None:
    bot = DownloadBot({})
    service = AttachmentService(
        settings(),
        cast(Any, bot),
        cast(AsyncOpenAI, SimpleNamespace()),
    )
    content: list[dict[str, Any]] = []
    album = [
        replace(
            item(1, "video"),
            file_name="clip-1.mp4",
            mime_type="video/mp4",
            caption="First clip",
        ),
        replace(
            item(2, "video"),
            file_name="clip-2.mp4",
            mime_type="video/mp4",
            caption="Second clip",
        ),
    ]

    file_ids = await service.add(text_message(), content, album=album)

    assert file_ids == ()
    assert content == [
        {
            "type": "input_text",
            "text": "Attached album item 1 of 2 caption:\nFirst clip",
        },
        {
            "type": "input_text",
            "text": (
                "Attached album item 1 of 2 video (clip-1.mp4): the model cannot view this video."
            ),
        },
        {
            "type": "input_text",
            "text": "Attached album item 2 of 2 caption:\nSecond clip",
        },
        {
            "type": "input_text",
            "text": (
                "Attached album item 2 of 2 video (clip-2.mp4): the model cannot view this video."
            ),
        },
    ]
