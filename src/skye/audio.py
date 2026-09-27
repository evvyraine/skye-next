from __future__ import annotations

import base64
import io
import json
import mimetypes
from typing import Any, Literal

import av
import httpx
import structlog
from openai import AsyncOpenAI, BadRequestError

from .config import Settings
from .fal import FalClient, FalError

log = structlog.get_logger()
_SPEECH_SAMPLE_RATE = 24_000
_SPEECH_FORMAT = "mp3"


class AudioService:
    """Speech in and out, on fal.ai or an OpenAI-compatible audio endpoint.

    Transcription sends the audio bytes and returns text. Speech returns audio
    bytes ready for a Telegram voice note: the compatible PCM path and the fal
    Gemini WAV output are both transcoded to MP3.
    """

    def __init__(
        self,
        *,
        transcription_model: str,
        speech_model: str,
        speech_voice: str,
        client: AsyncOpenAI | None = None,
        fal: FalClient | None = None,
        speech_response_format: Literal["opus", "pcm"] = "pcm",
    ) -> None:
        if (client is None) == (fal is None):
            raise ValueError("Configure exactly one audio backend.")
        self.transcription_model = transcription_model
        self.speech_model = speech_model
        self.speech_voice = speech_voice
        self.client = client
        self.fal = fal
        self.speech_response_format = speech_response_format

    @classmethod
    def from_settings(
        cls,
        config: Settings,
        *,
        client: AsyncOpenAI | None = None,
        fal: FalClient | None = None,
    ) -> AudioService:
        """Pick the fal model ids or the compatible ones from configuration."""
        if fal is not None:
            return cls(
                transcription_model=config.skye_fal_transcription_model,
                speech_model=config.skye_fal_speech_model,
                speech_voice=config.skye_speech_voice,
                fal=fal,
            )
        if client is not None:
            return cls(
                transcription_model=config.skye_transcription_model,
                speech_model=config.skye_speech_model,
                speech_voice=config.skye_speech_voice,
                client=client,
            )
        raise ValueError("Configure exactly one audio backend.")

    async def transcribe(self, filename: str, data: bytes, mime: str = "") -> str:
        if self.fal is not None:
            return await self._fal_transcribe(filename, data, mime)
        if self.client is None:
            raise RuntimeError("No audio provider is configured")
        return await transcribe_audio(self.client, self.transcription_model, filename, data)

    async def speak(self, text: str, instructions: str) -> bytes:
        if self.fal is not None:
            return await self._fal_speak(text, instructions)
        if self.client is None:
            raise RuntimeError("No audio provider is configured")
        return await self._compatible_speak(text, instructions)

    async def _fal_transcribe(self, filename: str, data: bytes, mime: str) -> str:
        assert self.fal is not None
        url = await self.fal.upload(data, mime or _audio_mime(filename), filename)
        result = await self.fal.run(
            self.transcription_model,
            {"audio_url": url, "tag_audio_events": False, "diarize": False},
        )
        text = result.get("text")
        if not isinstance(text, str):
            raise FalError("The speech provider returned no transcript.")
        return text.strip()

    async def _fal_speak(self, text: str, instructions: str) -> bytes:
        assert self.fal is not None
        # Gemini TTS treats a missing ``speakers``/``turns`` as dialogue and
        # then rejects ``prompt``; null them out to stay on single-speaker.
        payload: dict[str, Any] = {
            "prompt": text,
            "voice": self.speech_voice,
            "speakers": None,
            "turns": None,
        }
        if instructions:
            payload["style_instructions"] = instructions
        result = await self.fal.run(self.speech_model, payload)
        file = result.get("audio")
        url = file.get("url") if isinstance(file, dict) else None
        if not isinstance(url, str) or not url:
            raise FalError("The speech provider returned no audio.")
        data = await _download(url)
        if not data:
            raise FalError("The speech provider returned empty audio.")
        return to_mp3(data)

    async def _compatible_speak(self, text: str, instructions: str) -> bytes:
        assert self.client is not None
        try:
            response = await self.client.audio.speech.create(
                model=self.speech_model,
                voice=self.speech_voice,
                input=text,
                instructions=instructions,
                response_format=self.speech_response_format,
            )
        except BadRequestError:
            # Some TTS models (e.g. Gemini TTS via gateways) reject
            # instructions; retry once with plain text delivery.
            response = await self.client.audio.speech.create(
                model=self.speech_model,
                voice=self.speech_voice,
                input=text,
                response_format=self.speech_response_format,
            )
        audio = _unwrap_audio_payload(response.content)
        if audio and self.speech_response_format == "pcm":
            audio = _pcm_to_mp3(audio)
        return audio


async def transcribe_audio(client: AsyncOpenAI, model: str, filename: str, data: bytes) -> str:
    # aiohttp hands multipart bodies back as ``bytearray``; httpx only treats
    # ``bytes`` (or a file object) as an in-memory upload, so coerce first.
    # Without this the request body fails to serialize and never reaches the
    # provider.
    payload = bytes(data)
    result = await client.audio.transcriptions.create(
        model=model,
        file=(filename, payload),
        response_format="json",
    )
    return str(result.text).strip()


def _unwrap_audio_payload(audio: bytes) -> bytes:
    """Decode gateway JSON audio envelopes: {"audio": "<base64>", ...}.

    OpenAI returns raw audio bytes; some OpenAI-compatible gateways wrap the
    audio in a JSON envelope instead. Anything that is not JSON, or JSON
    without an audio field, passes through untouched.
    """
    stripped = audio.strip()
    if not stripped.startswith(b"{"):
        return audio
    try:
        payload = json.loads(stripped.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return audio
    encoded = payload.get("audio") if isinstance(payload, dict) else None
    if not isinstance(encoded, str) or not encoded:
        return audio
    try:
        return base64.b64decode(encoded, validate=True)
    except ValueError:
        return audio


def _pcm_to_mp3(audio: bytes) -> bytes:
    output = io.BytesIO()
    with av.open(output, mode="w", format=_SPEECH_FORMAT) as container:
        stream = container.add_stream("libmp3lame", rate=_SPEECH_SAMPLE_RATE)
        stream.layout = "mono"
        frame = av.AudioFrame(format="s16", layout="mono", samples=len(audio) // 2)
        frame.sample_rate = _SPEECH_SAMPLE_RATE
        frame.planes[0].update(audio)
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return output.getvalue()


def to_mp3(audio: bytes) -> bytes:
    """Decode any container (fal Gemini TTS returns WAV) and encode mono MP3."""
    output = io.BytesIO()
    resampler = av.AudioResampler(
        format="s16", layout="mono", rate=_SPEECH_SAMPLE_RATE
    )
    with av.open(io.BytesIO(audio), mode="r") as source, av.open(
        output, mode="w", format=_SPEECH_FORMAT
    ) as target:
        stream = target.add_stream("libmp3lame", rate=_SPEECH_SAMPLE_RATE)
        stream.layout = "mono"
        for frame in source.decode(audio=0):
            for resampled in resampler.resample(frame):
                for packet in stream.encode(resampled):
                    target.mux(packet)
        for resampled in resampler.resample(None):
            for packet in stream.encode(resampled):
                target.mux(packet)
        for packet in stream.encode():
            target.mux(packet)
    return output.getvalue()


def _audio_mime(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "audio/ogg"


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content
