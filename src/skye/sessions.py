from __future__ import annotations

import json
from typing import Any, cast

from agents.items import TResponseInputItem

from .db import Database


class DatabaseSession:
    """Durable full-fidelity Responses history with a bounded replay window."""

    session_settings = None

    def __init__(self, database: Database, session_id: str, max_chars: int) -> None:
        self.database = database
        self.session_id = session_id
        self.max_chars = max_chars

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        items = await self.database.session_items(self.session_id)
        if limit is not None:
            items = items[-limit:]
        selected: list[dict[str, Any]] = []
        size = 0
        for item in reversed(items):
            item_size = _session_item_chars(item)
            if selected and size + item_size > self.max_chars:
                break
            selected.append(item)
            size += item_size
        selected.reverse()
        return cast(list[TResponseInputItem], selected)

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        await self.database.add_session_items(self.session_id, cast(list[dict[str, Any]], items))

    async def pop_item(self) -> TResponseInputItem | None:
        return cast(
            TResponseInputItem | None, await self.database.pop_session_item(self.session_id)
        )

    async def clear_session(self) -> None:
        await self.database.clear_session(self.session_id)


def without_inline_payloads(value: Any) -> Any:
    """Drop base64 bodies so replay/size math is not dominated by one photo."""
    if isinstance(value, list):
        return [without_inline_payloads(item) for item in value]
    if not isinstance(value, dict):
        return value
    kind = value.get("type")
    if kind == "input_image":
        stripped = dict(value)
        if isinstance(stripped.get("image_url"), str):
            stripped["image_url"] = "data:image"
        return stripped
    if kind == "input_file":
        stripped = dict(value)
        if isinstance(stripped.get("file_data"), str):
            stripped["file_data"] = "data:file"
        return stripped
    if kind == "input_audio":
        stripped = dict(value)
        inner = stripped.get("input_audio")
        if isinstance(inner, dict):
            stripped["input_audio"] = {**inner, "data": ""}
        return stripped
    if kind in {"image_generation_call", "openrouter:image_generation"}:
        stripped = dict(value)
        stripped.pop("result", None)
        stripped.pop("imageUrl", None)
        return {key: without_inline_payloads(item) for key, item in stripped.items()}
    return {key: without_inline_payloads(item) for key, item in value.items()}


def _session_item_chars(item: dict[str, Any]) -> int:
    return len(json.dumps(without_inline_payloads(item), ensure_ascii=False, separators=(",", ":")))
