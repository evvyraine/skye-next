from __future__ import annotations

from typing import Any

import structlog
from fal_client import AsyncClient, FalClientError, StorageSettings

log = structlog.get_logger()

# Uploaded inputs (a user photo, a voice note) live on fal storage only long
# enough for the queued request to fetch them.
UPLOAD_LIFECYCLE = StorageSettings(expires_in="1d")


class FalError(RuntimeError):
    """A fal.ai request failed with a message safe to surface to the model."""


class FalClient:
    """Queue a fal.ai model, wait for it, and return its output.

    ``fal_client`` owns submission, polling, retries, and backup domains. This
    wrapper adds one place for the timeout and turns transport failures into
    ``FalError`` so the image and speech services share a single failure shape.
    """

    def __init__(self, api_key: str, *, timeout_seconds: float = 240.0) -> None:
        self._client = AsyncClient(key=api_key)
        self._timeout = timeout_seconds

    async def run(self, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await self._client.subscribe(
                model, payload, client_timeout=self._timeout
            )
        except FalClientError as error:
            log.warning("fal_request_failed", model=model, error=type(error).__name__)
            raise FalError(str(error) or "The media provider failed.") from error
        if not isinstance(result, dict):
            raise FalError("The media provider returned an unexpected answer.")
        return result

    async def upload(self, data: bytes, content_type: str, filename: str) -> str:
        try:
            return await self._client.upload(
                data, content_type, filename, lifecycle=UPLOAD_LIFECYCLE
            )
        except FalClientError as error:
            log.warning("fal_upload_failed", error=type(error).__name__)
            raise FalError("Could not send that file to the media provider.") from error
