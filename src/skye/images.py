from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog
from agents import FunctionTool, function_tool
from openai import AsyncOpenAI

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
    """Provider-independent pictures: Images API directly, files stay local."""

    def __init__(self, client: AsyncOpenAI, model: str, max_bytes: int) -> None:
        self.client = client
        self.model = model
        self.max_bytes = max_bytes

    async def generate(self, prompt: str) -> bytes:
        response = await self.client.images.generate(model=self.model, prompt=prompt)
        return await self._payload(response)

    async def edit(self, prompt: str, sources: list[tuple[str, bytes]]) -> bytes:
        files = [
            (f"source-{index}.{sniff_extension(sniff_mime(data))}", data, sniff_mime(data))
            for index, (_, data) in enumerate(sources)
        ]
        response = await self.client.images.edit(
            model=self.model,
            prompt=prompt,
            image=files[0] if len(files) == 1 else files,
        )
        return await self._payload(response)

    async def _payload(self, response: Any) -> bytes:
        data = response.data[0]
        encoded = getattr(data, "b64_json", None)
        if encoded:
            image = base64.b64decode(encoded, validate=True)
        else:
            url = getattr(data, "url", None)
            if not url:
                raise ValueError("The image provider returned no picture.")
            image = await _download(url)
        if not image:
            raise ValueError("The image provider returned an empty picture.")
        if len(image) > self.max_bytes:
            raise ValueError("The generated picture is too large.")
        return image


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
