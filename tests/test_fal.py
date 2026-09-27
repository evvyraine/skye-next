from __future__ import annotations

import io
from typing import Any, cast
from unittest.mock import AsyncMock

import av
import pytest
from fal_client import FalClientError

from skye.audio import AudioService
from skye.config import Settings
from skye.fal import FalClient, FalError
from skye.images import FalImageService

IMAGE_EDIT_MODEL = "openai/gpt-image-2.5/flare/edit"
SPEECH_MODEL = "google/gemini-3.8-flash-lite-tts"
TRANSCRIPTION_MODEL = "fal-ai/elevenlabs/speech-to-text/scribe-v2"


class FakeFal:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result or {}
        self.upload = AsyncMock(return_value="https://fal.test/file")
        self.runs: list[tuple[str, dict[str, Any]]] = []

    async def run(self, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.runs.append((model, payload))
        return self.result


def wav_bytes() -> bytes:
    output = io.BytesIO()
    with av.open(output, mode="w", format="wav") as container:
        stream = container.add_stream("pcm_s16le", rate=24_000)
        stream.layout = "mono"
        frame = av.AudioFrame(format="s16", layout="mono", samples=2_400)
        frame.sample_rate = 24_000
        frame.planes[0].update(b"\x00\x10" * 2_400)
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return output.getvalue()


async def test_fal_image_generate_returns_the_first_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fal = FakeFal({"images": [{"url": "https://fal.test/out.png"}]})
    service = FalImageService(cast(Any, fal), "gen/model", IMAGE_EDIT_MODEL, 1024)
    monkeypatch.setattr("skye.images._download", AsyncMock(return_value=b"png"))

    assert await service.generate("a cat") == b"png"
    assert fal.runs == [("gen/model", {"prompt": "a cat"})]


async def test_fal_image_edit_uploads_sources_and_passes_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fal = FakeFal({"images": [{"url": "https://fal.test/out.png"}]})
    service = FalImageService(cast(Any, fal), "gen/model", IMAGE_EDIT_MODEL, 1024)
    monkeypatch.setattr("skye.images._download", AsyncMock(return_value=b"png"))

    result = await service.edit("add a hat", [("attached-0", b"\x89PNG\r\n\x1a\nx")])

    assert result == b"png"
    model, payload = fal.runs[0]
    assert model == IMAGE_EDIT_MODEL
    assert payload == {"prompt": "add a hat", "image_urls": ["https://fal.test/file"]}
    content_type, filename = fal.upload.await_args.args[1:3]
    assert content_type == "image/png"
    assert filename.startswith("source-0.")


async def test_fal_image_rejects_an_oversized_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fal = FakeFal({"images": [{"url": "https://fal.test/out.png"}]})
    service = FalImageService(cast(Any, fal), "gen/model", IMAGE_EDIT_MODEL, 2)
    monkeypatch.setattr("skye.images._download", AsyncMock(return_value=b"too big"))

    with pytest.raises(ValueError, match="too large"):
        await service.generate("a cat")


async def test_fal_transcribe_uploads_audio_and_reads_text() -> None:
    fal = FakeFal({"text": "Hello there."})
    audio = AudioService(
        transcription_model=TRANSCRIPTION_MODEL,
        speech_model=SPEECH_MODEL,
        speech_voice="Aoede",
        fal=cast(Any, fal),
    )

    assert await audio.transcribe("voice.ogg", b"audio", "audio/ogg") == "Hello there."
    assert fal.upload.await_args.args[1] == "audio/ogg"
    model, payload = fal.runs[0]
    assert model == TRANSCRIPTION_MODEL
    assert payload["audio_url"] == "https://fal.test/file"
    assert payload["diarize"] is False
    assert payload["tag_audio_events"] is False


async def test_fal_speak_transcodes_wav_to_mp3(monkeypatch: pytest.MonkeyPatch) -> None:
    fal = FakeFal({"audio": {"url": "https://fal.test/out.wav"}})
    audio = AudioService(
        transcription_model=TRANSCRIPTION_MODEL,
        speech_model=SPEECH_MODEL,
        speech_voice="Aoede",
        fal=cast(Any, fal),
    )
    monkeypatch.setattr("skye.audio._download", AsyncMock(return_value=wav_bytes()))

    result = await audio.speak("Hello.", "Warm and calm.")

    assert result.startswith(b"ID3")
    assert fal.runs == [
        (
            SPEECH_MODEL,
            {
                "prompt": "Hello.",
                "voice": "Aoede",
                "style_instructions": "Warm and calm.",
                "speakers": None,
                "turns": None,
            },
        )
    ]


async def test_fal_speak_omits_empty_style_instructions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fal = FakeFal({"audio": {"url": "https://fal.test/out.wav"}})
    audio = AudioService(
        transcription_model=TRANSCRIPTION_MODEL,
        speech_model=SPEECH_MODEL,
        speech_voice="Aoede",
        fal=cast(Any, fal),
    )
    monkeypatch.setattr("skye.audio._download", AsyncMock(return_value=wav_bytes()))

    await audio.speak("Hello.", "")

    assert fal.runs[0][1] == {
        "prompt": "Hello.",
        "voice": "Aoede",
        "speakers": None,
        "turns": None,
    }


def test_audio_service_selects_fal_models_from_settings() -> None:
    config = Settings.model_construct(
        skye_fal_transcription_model=TRANSCRIPTION_MODEL,
        skye_fal_speech_model=SPEECH_MODEL,
        skye_speech_voice="Aoede",
    )

    service = AudioService.from_settings(config, fal=cast(Any, FakeFal()))

    assert service.transcription_model == TRANSCRIPTION_MODEL
    assert service.speech_model == SPEECH_MODEL
    assert service.speech_voice == "Aoede"


async def test_fal_client_subscribes_with_the_configured_timeout() -> None:
    captured: dict[str, Any] = {}

    class Underlying:
        async def subscribe(self, model: str, payload: dict[str, Any], **kwargs: Any) -> Any:
            captured.update({"model": model, "payload": payload, **kwargs})
            return {"images": []}

    client = FalClient("fal-test", timeout_seconds=12)
    client._client = cast(Any, Underlying())

    assert await client.run("m", {"prompt": "p"}) == {"images": []}
    assert captured["model"] == "m"
    assert captured["client_timeout"] == 12


async def test_fal_client_turns_transport_errors_into_fal_errors() -> None:
    class Broken:
        async def subscribe(self, *args: Any, **kwargs: Any) -> Any:
            raise FalClientError("boom")

    client = FalClient("fal-test")
    client._client = cast(Any, Broken())

    with pytest.raises(FalError, match="boom"):
        await client.run("m", {})
