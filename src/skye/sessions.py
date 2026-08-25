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
            item_size = len(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
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
