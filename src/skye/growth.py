from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .db import Database

TRIAL_SECONDS = 7 * 86_400
SOURCE_PATTERN = re.compile(r"(?:src|ref)_([A-Za-z0-9_-]{1,48})\Z")


@dataclass(frozen=True, slots=True)
class ActivationProgress:
    tasks: int
    active_days: int
    used_rich_capability: bool

    @property
    def activated(self) -> bool:
        return self.tasks >= 3 and self.active_days >= 2 and self.used_rich_capability


class GrowthService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def started(self, user_id: int, payload: str | None) -> None:
        source = self.source(payload)
        await self.database.record_product_event(user_id, "started", source=source)

    async def completed_task(
        self,
        user_id: int,
        capability: str,
        *,
        occurred_at: int | None = None,
    ) -> bool:
        now = int(time.time()) if occurred_at is None else occurred_at
        await self.database.record_product_event(
            user_id,
            "task_completed",
            capability=capability,
            occurred_at=now,
        )
        return await self.database.grant_earned_trial(user_id, now, TRIAL_SECONDS)

    async def progress(self, user_id: int) -> ActivationProgress:
        tasks, active_days, rich = await self.database.activation_progress(user_id)
        return ActivationProgress(tasks, active_days, rich)

    @staticmethod
    def source(payload: str | None) -> str | None:
        if payload is None:
            return None
        match = SOURCE_PATTERN.fullmatch(payload)
        return None if match is None else match.group(1)
