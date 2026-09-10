from __future__ import annotations

import base64
import html
import mimetypes
import re
import zipfile
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
AUDIO_FORMATS = {
    ".aac": "aac",
    ".aif": "aiff",
    ".aiff": "aiff",
    ".flac": "flac",
    ".m4a": "m4a",
    ".mp3": "mp3",
    ".mpeg": "mp3",
    ".oga": "ogg",
    ".ogg": "ogg",
    ".opus": "ogg",
    ".wav": "wav",
    ".wave": "wav",
}
AUDIO_MIMES = {
    "audio/aac": "aac",
    "audio/aiff": "aiff",
    "audio/flac": "flac",
    "audio/m4a": "m4a",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/wav": "wav",
    "audio/wave": "wav",
    "audio/x-aiff": "aiff",
    "audio/x-wav": "wav",
}
PDF_MIMES = {"application/pdf"}
TEXT_MIMES = {
    "application/csv",
    "application/javascript",
    "application/json",
    "application/ld+json",
    "application/sql",
    "application/toml",
    "application/x-httpd-php",
    "application/x-sh",
    "application/x-yaml",
    "application/xml",
    "application/yaml",
}
TEXT_EXTENSIONS = {
    ".bat",
    ".c",
    ".cfg",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".env",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".log",
    ".lua",
    ".md",
    ".php",
    ".pl",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".svg",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}
MAX_DOCUMENT_CHARS = 100_000


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
        content.extend(
            [
                {"type": "input_text", "text": f"{label} image:"},
                image_input_part("image/jpeg", data),
            ]
        )
        return None

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
        transcript = await transcribe_audio(
            self.client, self.config.skye_transcription_model, filename, data
        )
        content.extend(
            audio_model_parts(
                label,
                kind,
                filename,
                mime,
                data,
                transcript,
                native_media=self.config.skye_native_media,
            )
        )
        return None

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
        extension = Path(filename).suffix.lower()
        if mime.startswith("audio/") or extension in AUDIO_EXTENSIONS:
            transcript = await transcribe_audio(
                self.client, self.config.skye_transcription_model, filename, data
            )
            content.extend(
                audio_model_parts(
                    label,
                    "audio",
                    filename,
                    mime,
                    data,
                    transcript,
                    native_media=self.config.skye_native_media,
                )
            )
            return None
        if mime.startswith("image/") or mime in IMAGE_MIMES:
            content.extend(
                [
                    {"type": "input_text", "text": f"{label} image ({filename}):"},
                    image_input_part(mime, data),
                ]
            )
            return None
        content.extend(
            document_model_parts(
                label,
                filename,
                mime,
                data,
                native_media=self.config.skye_native_media,
            )
        )
        return None

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


def data_url(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def openai_file_parts(
    filename: str,
    mime: str,
    data: bytes,
    transcript: str | None = None,
    *,
    native_media: bool = False,
) -> list[dict[str, Any]]:
    if mime.startswith("image/") or mime in IMAGE_MIMES:
        return [
            {"type": "input_text", "text": f"Attached image ({filename}):"},
            image_input_part(mime, data),
        ]
    if transcript is not None:
        return audio_model_parts(
            "Attached",
            "audio",
            filename,
            mime,
            data,
            transcript,
            native_media=native_media,
        )
    return document_model_parts(
        "Attached", filename, mime, data, native_media=native_media
    )


def document_model_parts(
    label: str,
    filename: str,
    mime: str,
    data: bytes,
    *,
    native_media: bool = False,
) -> list[dict[str, Any]]:
    """Turn a document into model input that works on text-first providers.

    PDFs stay native file parts (OpenRouter and OpenAI parse them). Text-like
    files, and Office Open XML documents, are extracted locally and sent as
    text. Other binaries fall back to a native file part only when the operator
    opts in; otherwise the model gets a placeholder instead of a rejected part.
    """
    text = extract_document_text(filename, mime, data)
    if text is not None:
        return [{"type": "input_text", "text": f"{label} document ({filename}):\n{text}"}]
    native = [
        {"type": "input_text", "text": f"{label} document ({filename}):"},
        file_input_part(filename, mime, data),
    ]
    if _is_pdf(filename, mime) or native_media:
        return native
    return [
        {
            "type": "input_text",
            "text": (
                f"{label} document ({filename}) is attached, but this file type "
                "cannot be read inline."
            ),
        }
    ]


def extract_document_text(filename: str, mime: str, data: bytes) -> str | None:
    """Return readable text for text-like and Office Open XML documents."""
    extension = Path(filename).suffix.lower()
    if _is_pdf(filename, mime):
        return None
    if mime.startswith("text/") or mime in TEXT_MIMES or extension in TEXT_EXTENSIONS:
        return _decode_text(data)
    if extension == ".docx":
        return _office_text(data, "word/document.xml", r"</w:p>")
    if extension == ".pptx":
        return _office_text(data, ("ppt/slides/",), None)
    if extension == ".xlsx":
        return _spreadsheet_text(data)
    if b"\x00" not in data[:4096]:
        return _decode_text(data)
    return None


def _is_pdf(filename: str, mime: str) -> bool:
    return mime in PDF_MIMES or Path(filename).suffix.lower() == ".pdf"


def _decode_text(data: bytes) -> str | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\x00" in text:
        return None
    text = text.strip()
    if not text:
        return None
    return text[:MAX_DOCUMENT_CHARS]


def _office_text(
    data: bytes,
    member: str | tuple[str, ...],
    paragraph_tag: str | None,
) -> str | None:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = archive.namelist()
            if isinstance(member, str):
                selected = [name for name in names if name == member]
            else:
                selected = [
                    name
                    for name in names
                    if name.endswith(".xml")
                    and any(name.startswith(prefix) for prefix in member)
                ]
            chunks: list[str] = []
            for name in selected:
                xml = archive.read(name).decode("utf-8", "replace")
                if paragraph_tag:
                    xml = xml.replace(paragraph_tag, "\n")
                chunks.append(_strip_markup(xml))
    except (KeyError, ValueError, zipfile.BadZipFile):
        return None
    text = "\n".join(chunk for chunk in chunks if chunk).strip()
    return text[:MAX_DOCUMENT_CHARS] or None


def _spreadsheet_text(data: bytes) -> str | None:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            values: list[str] = []
            for name in archive.namelist():
                if name == "xl/sharedStrings.xml":
                    xml = archive.read(name).decode("utf-8", "replace")
                    values.extend(re.findall(r"<t[^>]*>(.*?)</t>", xml, re.S))
    except (KeyError, ValueError, zipfile.BadZipFile):
        return None
    text = "\n".join(html.unescape(value).strip() for value in values if value.strip())
    return text[:MAX_DOCUMENT_CHARS] or None


def _strip_markup(xml: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", xml)).strip()


def image_input_part(mime: str, data: bytes) -> dict[str, Any]:
    return {
        "type": "input_image",
        "detail": "auto",
        "image_url": data_url(mime or "image/jpeg", data),
    }


def file_input_part(filename: str, mime: str, data: bytes) -> dict[str, Any]:
    extra = {"detail": "auto"} if Path(filename).suffix.lower() == ".pdf" else {}
    return {
        "type": "input_file",
        "filename": filename,
        "file_data": data_url(mime or "application/octet-stream", data),
        **extra,
    }


def audio_model_parts(
    label: str,
    kind: str,
    filename: str,
    mime: str,
    data: bytes,
    transcript: str,
    *,
    native_media: bool = False,
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": f"{label} {kind} transcript ({filename}):\n{transcript}",
        }
    ]
    if kind == "audio" and native_media:
        audio = audio_input_part(filename, mime, data)
        if audio is not None:
            parts.append(audio)
    return parts


def audio_input_part(filename: str, mime: str, data: bytes) -> dict[str, Any] | None:
    fmt = audio_format(filename, mime)
    if fmt is None:
        return None
    return {
        "type": "input_audio",
        "input_audio": {"data": base64.b64encode(data).decode(), "format": fmt},
    }


def audio_format(filename: str, mime: str) -> str | None:
    extension = Path(filename).suffix.lower()
    if extension in AUDIO_FORMATS:
        return AUDIO_FORMATS[extension]
    return AUDIO_MIMES.get(mime.lower())


def is_audio_upload(filename: str, mime: str) -> bool:
    extension = Path(filename).suffix.lower()
    return mime.startswith("audio/") or extension in AUDIO_EXTENSIONS
