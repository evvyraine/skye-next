from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog
from agents import FunctionTool, function_tool
from openai import APIError, AsyncOpenAI

log = structlog.get_logger()
MAX_SOURCE_IMAGES = 4


def sniff_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return "image/png"


def sniff_extension(mime: str) -> str:
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
    }.get(mime, "png")


class ImageService:
    """Provider-independent pictures: Images API directly, files stay local.

    Gateway compatibility is response-driven, never per-provider: raw JSON is
    inspected so task-style answers (an id to poll) and media-style answers
    (a url or base64 payload) work the same. When the standard edits route is
    absent, a media request carrying the source pictures is tried once.
    """

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        max_bytes: int,
        *,
        poll_interval_seconds: float = 4.0,
        poll_timeout_seconds: float = 180.0,
    ) -> None:
        self.client = client
        self.model = model
        self.max_bytes = max_bytes
        self.poll_interval_seconds = poll_interval_seconds
        self.poll_timeout_seconds = poll_timeout_seconds

    async def generate(self, prompt: str) -> bytes:
        payload = await self.client.post(
            "/images/generations",
            body={"model": self.model, "prompt": prompt},
            cast_to=dict[str, Any],
        )
        return await self._resolve(payload)

    async def edit(self, prompt: str, sources: list[tuple[str, bytes]]) -> bytes:
        files = [
            (
                "image",
                (
                    f"source-{index}.{sniff_extension(sniff_mime(data))}",
                    data,
                    sniff_mime(data),
                ),
            )
            for index, (_, data) in enumerate(sources)
        ]
        try:
            payload = await self.client.post(
                "/images/edits",
                body={"model": self.model, "prompt": prompt},
                files=files,
                cast_to=dict[str, Any],
            )
        except APIError as error:
            if not _edits_unsupported(error):
                raise
            log.info("image_edit_route_missing", status=getattr(error, "status_code", None))
            payload = await self._edit_fallback(prompt, sources)
        return await self._resolve(payload)

    async def _edit_fallback(
        self, prompt: str, sources: list[tuple[str, bytes]]
    ) -> dict[str, Any]:
        """Edit through a gateway's media route when ``/images/edits`` is absent.

        OpenRouter exposes image editing on ``POST /images`` with
        ``input_references``. Older gateways use a task-style ``/media`` route.
        """
        try:
            return await self.client.post(
                "/images",
                body={
                    "model": self.model,
                    "prompt": prompt,
                    "input_references": [
                        {"type": "image_url", "image_url": {"url": _data_url(data)}}
                        for _, data in sources
                    ],
                },
                cast_to=dict[str, Any],
            )
        except APIError as error:
            if not _edits_unsupported(error):
                raise
            log.info(
                "image_route_missing",
                route="/images",
                status=getattr(error, "status_code", None),
            )
        return await self.client.post(
            "/media",
            body={
                "model": self.model,
                "input": {
                    "prompt": prompt,
                    "images": [
                        {"type": "base64", "data": _data_url(data)} for _, data in sources
                    ],
                },
            },
            cast_to=dict[str, Any],
        )

    async def _resolve(self, payload: dict[str, Any]) -> bytes:
        """Turn a generation answer into picture bytes.

        Accepts a direct Images payload, a completed media object, or a task
        id that is polled until the picture (or a failure) arrives.
        """
        direct = _payload_url_or_b64(payload)
        if direct is not None:
            return await self._bytes(direct)
        task_id = payload.get("requestId") or payload.get("id")
        if isinstance(task_id, str) and task_id:
            return await self._poll(task_id)
        raise ValueError("The image provider returned no picture.")

    async def _poll(self, task_id: str) -> bytes:
        deadline = time.monotonic() + self.poll_timeout_seconds
        while True:
            payload = await self.client.get(f"/media/{task_id}", cast_to=dict[str, Any])
            status = payload.get("status")
            if status == "completed":
                direct = _payload_url_or_b64(payload)
                if direct is None:
                    raise ValueError("The image provider returned no picture.")
                return await self._bytes(direct)
            if status in {"failed", "cancelled"}:
                detail = ""
                error = payload.get("error")
                if isinstance(error, dict) and error.get("message"):
                    detail = f": {error['message']}"
                raise ValueError(f"The image provider failed{detail}.")
            if time.monotonic() >= deadline:
                raise ValueError("The image provider timed out.")
            await asyncio.sleep(self.poll_interval_seconds)

    async def _bytes(self, direct: tuple[str, str]) -> bytes:
        kind, value = direct
        image = base64.b64decode(value, validate=True) if kind == "b64" else await _download(value)
        if not image:
            raise ValueError("The image provider returned an empty picture.")
        if len(image) > self.max_bytes:
            raise ValueError("The generated picture is too large.")
        return image


def _edits_unsupported(error: APIError) -> bool:
    """Whether a failed edits call means the route itself is unavailable.

    Gateways without an edits route answer 404/405/415/501, or 400 when they
    cannot parse the multipart body as JSON. Anything else (moderation,
    billing, bad prompt) is a real failure and must propagate.
    """
    status = getattr(error, "status_code", None)
    if status in {404, 405, 415, 501}:
        return True
    if status != 400:
        return False
    text = str(error).lower()
    markers = ("json", "route", "маршрут", "not found", "unknown", "unsupported", "формат")
    return any(marker in text for marker in markers)


def _payload_url_or_b64(payload: Any) -> tuple[str, str] | None:
    """Extract ("url" | "b64", value) from Images or media answer shapes."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        data = [data]
    if isinstance(data, list) and data and isinstance(data[0], dict):
        first = data[0]
        encoded = first.get("b64_json")
        if isinstance(encoded, str) and encoded:
            return ("b64", encoded)
        url = first.get("url")
        if isinstance(url, str) and url:
            return ("url", url)
    return None


def _data_url(data: bytes) -> str:
    mime = sniff_mime(data)
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content


async def turn_sources(
    user_input: str | list[Any],
    client: AsyncOpenAI,
    max_bytes: int,
) -> list[tuple[str, bytes]]:
    """Collect attached turn pictures for edit_image: data URLs inline,
    provider file ids downloaded."""
    found: list[tuple[str, bytes]] = []

    async def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                await visit(item)
            return
        if not isinstance(value, dict):
            return
        if value.get("type") == "input_image":
            image = await _part_image(value, client)
            if image is not None and len(image) <= max_bytes:
                found.append((f"attached-{len(found)}", image))
            if len(found) >= MAX_SOURCE_IMAGES:
                return
        for item in value.values():
            if len(found) >= MAX_SOURCE_IMAGES:
                return
            await visit(item)

    await visit(user_input)
    return found


async def _part_image(part: dict[str, Any], client: AsyncOpenAI) -> bytes | None:
    url = part.get("image_url")
    if isinstance(url, str) and url.startswith("data:") and ";base64," in url:
        try:
            return base64.b64decode(url.split(",", 1)[1], validate=True)
        except ValueError:
            log.warning("turn_image_decode_failed")
            return None
    file_id = part.get("file_id")
    if isinstance(file_id, str) and file_id:
        try:
            content = await client.files.content(file_id)
        except Exception as error:
            log.warning("turn_image_download_failed", error=type(error).__name__)
            return None
        data = getattr(content, "content", None)
        if isinstance(data, bytes):
            return data
        read = getattr(content, "read", None)
        if callable(read):
            result = read()
            if isinstance(result, bytes):
                return result
    return None


@dataclass(slots=True)
class TurnImages:
    """Per-turn image budget. Finished pictures are delivered by the runtime."""

    service: ImageService
    limit: int
    sources: list[tuple[str, bytes]] = field(default_factory=list)
    images: list[bytes] = field(default_factory=list)
    calls: int = 0

    def tools(self) -> list[FunctionTool]:
        turn = self

        @function_tool
        async def generate_image(prompt: str) -> str:
            """Create a new picture from a text description.

            The finished picture is delivered to the user automatically.
            Make exactly one call for a singular request.

            Args:
                prompt: What the picture should show.
            """
            if turn.calls >= turn.limit:
                return f"Image limit reached for this turn ({turn.limit})."
            if not prompt.strip():
                return "Describe the picture first."
            try:
                turn.images.append(await turn.service.generate(prompt.strip()))
            except Exception as error:
                log.warning("image_generate_failed", error=type(error).__name__)
                return "Couldn't create that picture. Try a different description."
            turn.calls += 1
            return f"Picture {len(turn.images)} of {turn.limit} is ready."

        @function_tool
        async def edit_image(prompt: str) -> str:
            """Change a photo attached to the current message.

            The finished picture is delivered to the user automatically.
            Use this only when the user attached a photo or replied to one.

            Args:
                prompt: What to change in the attached photo.
            """
            if turn.calls >= turn.limit:
                return f"Image limit reached for this turn ({turn.limit})."
            if not turn.sources:
                return "No photo is attached to this message. Ask for one first."
            if not prompt.strip():
                return "Describe the change first."
            try:
                turn.images.append(await turn.service.edit(prompt.strip(), turn.sources))
            except Exception as error:
                log.warning("image_edit_failed", error=type(error).__name__)
                return "Couldn't edit that photo. Try a different change."
            turn.calls += 1
            return f"Picture {len(turn.images)} of {turn.limit} is ready."

        return [generate_image, edit_image]
