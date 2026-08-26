from __future__ import annotations

import base64
import mimetypes
from collections.abc import Awaitable, Sequence
from io import BytesIO
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.types import Animation, Audio, Document, Message, PhotoSize, Video, VideoNote, Voice
from openai import AsyncOpenAI

from .config import Settings
from .models import MediaGroupItem

AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".mp4", ".mpeg", ".ogg", ".wav", ".webm"}


class AttachmentService:
    """Turn Telegram media into native OpenAI response inputs."""

    def __init__(self, config: Settings, bot: Bot, client: AsyncOpenAI) -> None:
        self.config = config
        self.bot = bot
        self.client = client

    async def add(
        self,
        message: Message,
        content: list[dict[str, Any]],
        *,
        album: Sequence[MediaGroupItem] = (),
    ) -> tuple[str, ...]:
        seen: set[str] = set()
        file_ids: list[str] = []
        total = len(album)
        for index, item in enumerate(album, start=1):
            label = f"Attached album item {index} of {total}"
            if item.caption and item.message_id != message.message_id:
                content.append({"type": "input_text", "text": f"{label} caption:\n{item.caption}"})
            if item.media_kind == "photo":
                await self._collect(
                    file_ids,
                    self._photo(
                        item,
                        f"{label} image",
                        content,
                        seen,
                        upload_filename=f"album-{item.message_id}.jpg",
                    ),
                )
            elif item.media_kind in {"audio", "voice", "video_note"}:
                await self._collect(file_ids, self._audio(item, label, content, seen))
            elif item.media_kind == "document":
                await self._collect(file_ids, self._document(item, label, content, seen))
            elif item.media_kind == "video":
                self._video(item, label, content, seen)
        for label, source in (("Attached", message), ("Replied-to", message.reply_to_message)):
            if source is None:
                continue
            if source.photo:
                photo = source.photo[-1]
                await self._collect(
                    file_ids,
                    self._photo(
                        photo,
                        label,
                        content,
                        seen,
                        upload_filename=f"image-{photo.file_unique_id}.jpg",
                    ),
                )
            if source.voice:
                await self._collect(file_ids, self._audio(source.voice, label, content, seen))
            if source.audio:
                await self._collect(file_ids, self._audio(source.audio, label, content, seen))
            if source.video_note:
                await self._collect(file_ids, self._audio(source.video_note, label, content, seen))
            if source.video:
                caption = source.caption if source is not message else None
                self._video(source.video, label, content, seen, caption=caption)
            if source.animation:
                caption = source.caption if source is not message else None
                self._video(source.animation, label, content, seen, caption=caption)
            if source.document:
                await self._collect(file_ids, self._document(source.document, label, content, seen))
        return tuple(file_ids)

    async def _photo(
        self,
        photo: PhotoSize | MediaGroupItem,
        label: str,
        content: list[dict[str, Any]],
        seen: set[str],
        upload_filename: str = "image.jpg",
    ) -> str | None:
        if not self._new(photo.file_unique_id, seen):
            return None
        data = await self._download(photo, photo.file_size, "image")
        file_id = await upload_openai_file(self.client, upload_filename, "image/jpeg", data)
        image: dict[str, Any] = {"type": "input_image"}
        if file_id:
            image["file_id"] = file_id
        else:
            image["image_url"] = data_url("image/jpeg", data)
        image["detail"] = "auto"
        content.extend(
            [
                {"type": "input_text", "text": f"{label} image:"},
                image,
            ]
        )
        return file_id

    async def _audio(
        self,
        audio: Voice | Audio | VideoNote | MediaGroupItem,
        label: str,
        content: list[dict[str, Any]],
        seen: set[str],
    ) -> str | None:
        if not self._new(audio.file_unique_id, seen):
            return None
        filename = getattr(audio, "file_name", None) or (
            "voice.ogg"
            if isinstance(audio, Voice) or getattr(audio, "media_kind", None) == "voice"
            else "video-note.mp4"
            if isinstance(audio, VideoNote) or getattr(audio, "media_kind", None) == "video_note"
            else "audio.mp3"
        )
        kind = (
            "video message"
            if isinstance(audio, VideoNote) or getattr(audio, "media_kind", None) == "video_note"
            else "audio"
        )
        data = await self._download(audio, audio.file_size, kind)
        mime = getattr(audio, "mime_type", None) or "audio/ogg"
        upload_filename = (
            f"album-{audio.message_id}-{filename}"
            if isinstance(audio, MediaGroupItem)
            else filename
        )
        file_id = await upload_openai_file(self.client, upload_filename, mime, data)
        transcript = await transcribe_audio(
            self.client, self.config.skye_transcription_model, filename, data
        )
        content.append(
            {
                "type": "input_text",
                "text": f"{label} {kind} transcript ({filename}):\n{transcript}",
            }
        )
        return file_id

    async def _document(
        self,
        document: Document | MediaGroupItem,
        label: str,
        content: list[dict[str, Any]],
        seen: set[str],
    ) -> str | None:
        if not self._new(document.file_unique_id, seen):
            return None
        suffix = mimetypes.guess_extension(document.mime_type or "") or ""
        filename = getattr(document, "file_name", None) or f"document{suffix}"
        mime = getattr(document, "mime_type", None) or "application/octet-stream"
        if mime.startswith("video/"):
            self._video(document, label, content, seen, already_seen=True)
            return None
        data = await self._download(document, getattr(document, "file_size", None), "document")
        upload_filename = (
            f"album-{document.message_id}-{filename}"
            if isinstance(document, MediaGroupItem)
            else filename
        )
        file_id = await upload_openai_file(self.client, upload_filename, mime, data)
        extension = Path(filename).suffix.lower()
        if mime.startswith("audio/") or extension in AUDIO_EXTENSIONS:
            transcript = await transcribe_audio(
                self.client, self.config.skye_transcription_model, filename, data
            )
            content.append(
                {
                    "type": "input_text",
                    "text": f"{label} audio transcript ({filename}):\n{transcript}",
                }
            )
            return file_id
        file_part: dict[str, Any] = {
            "type": "input_file",
            "filename": filename,
        }
        if file_id:
            file_part["file_id"] = file_id
        else:
            file_part["file_data"] = data_url(mime, data)
        content.extend(
            [
                {"type": "input_text", "text": f"{label} document ({filename}):"},
                {**file_part, **({"detail": "auto"} if extension == ".pdf" else {})},
            ]
        )
        return file_id

    def _video(
        self,
        media: Animation | Document | Video | MediaGroupItem,
        label: str,
        content: list[dict[str, Any]],
        seen: set[str],
        *,
        caption: str | None = None,
        already_seen: bool = False,
    ) -> None:
        if not already_seen and not self._new(media.file_unique_id, seen):
            return
        filename = getattr(media, "file_name", None) or "video.mp4"
        text = f"{label} video ({filename}): the model cannot view this video."
        if caption:
            text += f"\nCaption:\n{caption}"
        content.append({"type": "input_text", "text": text})

    @staticmethod
    async def _collect(file_ids: list[str], pending: Awaitable[str | None]) -> None:
        file_id = await pending
        if file_id:
            file_ids.append(file_id)

    async def _download(self, media: Any, size: int | None, kind: str) -> bytes:
        limit = self.config.skye_max_attachment_bytes
        if size and size > limit:
            raise ValueError(f"That {kind} is too large (maximum {limit // 1024 // 1024} MB).")
        destination = BytesIO()
        await self.bot.download(media, destination=destination)
        data = destination.getvalue()
        if len(data) > limit:
            raise ValueError(f"That {kind} is too large (maximum {limit // 1024 // 1024} MB).")
        return data

    @staticmethod
    def _data_url(mime: str, data: bytes) -> str:
        return data_url(mime, data)

    @staticmethod
    def _new(file_unique_id: str, seen: set[str]) -> bool:
        if file_unique_id in seen:
            return False
        seen.add(file_unique_id)
        return True


IMAGE_MIMES = {"image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp"}


async def transcribe_audio(client: AsyncOpenAI, model: str, filename: str, data: bytes) -> str:
    result = await client.audio.transcriptions.create(
        model=model,
        file=(filename, data),
        response_format="json",
    )
    return str(result.text).strip()


async def upload_openai_file(
    client: AsyncOpenAI, filename: str, mime: str, data: bytes
) -> str | None:
    files = getattr(client, "files", None)
    create = getattr(files, "create", None)
    if create is None:
        return None
    uploaded = await create(file=(filename, data, mime), purpose="user_data")
    file_id = getattr(uploaded, "id", None)
    return str(file_id) if file_id else None


def data_url(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def openai_file_parts(
    filename: str,
    mime: str,
    data: bytes,
    transcript: str | None = None,
    file_id: str | None = None,
) -> list[dict[str, Any]]:
    extension = Path(filename).suffix.lower()
    if mime.startswith("image/") or mime in IMAGE_MIMES:
        image: dict[str, Any] = {"type": "input_image", "detail": "auto"}
        if file_id:
            image["file_id"] = file_id
        else:
            image["image_url"] = data_url(mime or "image/jpeg", data)
        return [
            {"type": "input_text", "text": f"Attached image ({filename}):"},
            image,
        ]
    if transcript is not None:
        parts: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": f"Attached audio transcript ({filename}):\n{transcript}",
            }
        ]
        return parts
    if file_id:
        file_part = {"type": "input_file", "file_id": file_id}
    else:
        file_part = {
            "type": "input_file",
            "filename": filename,
            "file_data": data_url(mime or "application/octet-stream", data),
        }
    return [
        {"type": "input_text", "text": f"Attached document ({filename}):"},
        {**file_part, **({"detail": "auto"} if extension == ".pdf" else {})},
    ]


def is_audio_upload(filename: str, mime: str) -> bool:
    extension = Path(filename).suffix.lower()
    return mime.startswith("audio/") or extension in AUDIO_EXTENSIONS
