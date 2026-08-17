from __future__ import annotations

import base64
import mimetypes
from io import BytesIO
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.types import Audio, Document, Message, PhotoSize, VideoNote, Voice
from openai import AsyncOpenAI

from .config import Settings

AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".mp4", ".mpeg", ".ogg", ".wav", ".webm"}


class AttachmentService:
    """Turn Telegram media into native OpenAI response inputs."""

    def __init__(self, config: Settings, bot: Bot, client: AsyncOpenAI) -> None:
        self.config = config
        self.bot = bot
        self.client = client

    async def add(self, message: Message, content: list[dict[str, Any]]) -> None:
        seen: set[str] = set()
        for label, source in (("Attached", message), ("Replied-to", message.reply_to_message)):
            if source is None:
                continue
            if source.photo:
                await self._photo(source.photo[-1], label, content, seen)
            if source.voice:
                await self._audio(source.voice, label, content, seen)
            if source.audio:
                await self._audio(source.audio, label, content, seen)
            if source.video_note:
                await self._audio(source.video_note, label, content, seen)
            if source.document:
                await self._document(source.document, label, content, seen)

    async def _photo(
        self,
        photo: PhotoSize,
        label: str,
        content: list[dict[str, Any]],
        seen: set[str],
    ) -> None:
        if not self._new(photo.file_unique_id, seen):
            return
        data = await self._download(photo, photo.file_size, "image")
        content.extend(
            [
                {"type": "input_text", "text": f"{label} image:"},
                {
                    "type": "input_image",
                    "image_url": data_url("image/jpeg", data),
                    "detail": "auto",
                },
            ]
        )

    async def _audio(
        self,
        audio: Voice | Audio | VideoNote,
        label: str,
        content: list[dict[str, Any]],
        seen: set[str],
    ) -> None:
        if not self._new(audio.file_unique_id, seen):
            return
        filename = getattr(audio, "file_name", None) or (
            "voice.ogg"
            if isinstance(audio, Voice)
            else "video-note.mp4"
            if isinstance(audio, VideoNote)
            else "audio.mp3"
        )
        kind = "video message" if isinstance(audio, VideoNote) else "audio"
        data = await self._download(audio, audio.file_size, kind)
        transcript = await transcribe_audio(
            self.client, self.config.skye_transcription_model, filename, data
        )
        content.append(
            {
                "type": "input_text",
                "text": f"{label} {kind} transcript ({filename}):\n{transcript}",
            }
        )

    async def _document(
        self,
        document: Document,
        label: str,
        content: list[dict[str, Any]],
        seen: set[str],
    ) -> None:
        if not self._new(document.file_unique_id, seen):
            return
        suffix = mimetypes.guess_extension(document.mime_type or "") or ""
        filename = document.file_name or f"document{suffix}"
        mime = document.mime_type or "application/octet-stream"
        data = await self._download(document, document.file_size, "document")
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
            return
        content.extend(
            [
                {"type": "input_text", "text": f"{label} document ({filename}):"},
                {
                    "type": "input_file",
                    "filename": filename,
                    "file_data": data_url(mime, data),
                    **({"detail": "auto"} if extension == ".pdf" else {}),
                },
            ]
        )

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


def data_url(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def openai_file_parts(
    filename: str, mime: str, data: bytes, transcript: str | None = None
) -> list[dict[str, Any]]:
    extension = Path(filename).suffix.lower()
    if mime.startswith("image/") or mime in IMAGE_MIMES:
        return [
            {"type": "input_text", "text": f"Attached image ({filename}):"},
            {"type": "input_image", "image_url": data_url(mime or "image/jpeg", data),
             "detail": "auto"},
        ]
    if transcript is not None:
        return [
            {
                "type": "input_text",
                "text": f"Attached audio transcript ({filename}):\n{transcript}",
            }
        ]
    return [
        {"type": "input_text", "text": f"Attached document ({filename}):"},
        {
            "type": "input_file",
            "filename": filename,
            "file_data": data_url(mime or "application/octet-stream", data),
            **({"detail": "auto"} if extension == ".pdf" else {}),
        },
    ]


def is_audio_upload(filename: str, mime: str) -> bool:
    extension = Path(filename).suffix.lower()
    return mime.startswith("audio/") or extension in AUDIO_EXTENSIONS
