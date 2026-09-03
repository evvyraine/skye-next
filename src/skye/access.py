from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from .db import Database
from .models import RequestContext, Scope

ADMIN_CACHE_TTL = 90.0


@dataclass(frozen=True, slots=True)
class ChatAdministrator:
    user_id: int
    is_creator: bool
    is_bot: bool = False


ListChatAdministrators = Callable[[int], Awaitable[Sequence[ChatAdministrator] | None]]


class AccessService:
    def __init__(
        self,
        database: Database,
        owner_ids: frozenset[int],
        *,
        list_administrators: ListChatAdministrators | None = None,
        admin_cache_ttl: float = ADMIN_CACHE_TTL,
    ) -> None:
        self.database = database
        self.owner_ids = owner_ids
        self._list_administrators = list_administrators
        self._admin_cache_ttl = admin_cache_ttl
        self._admin_cache: dict[int, tuple[float, tuple[ChatAdministrator, ...]]] = {}

    def is_owner(self, user_id: int) -> bool:
        return user_id in self.owner_ids

    async def allowed(self, context: RequestContext) -> bool:
        if self.is_owner(context.user_id):
            return True
        if await self.database.access_effect(Scope("user", context.user_id)) == "ban":
            return False
        if context.chat_type == "private":
            return True
        if await self.database.access_effect(context.scope) == "allow":
            return True
        return await self.group_payer(context.chat_id) is not None

    async def plus(self, context: RequestContext) -> bool:
        if not await self.allowed(context):
            return False
        if self.is_owner(context.user_id):
            return True
        if await self.database.access_effect(context.scope) == "allow":
            return True
        return await self.payer_qualifies(context.user_id)

    async def billed_user_id(self, context: RequestContext) -> int:
        if context.chat_type == "private":
            return context.user_id
        if await self.database.access_effect(context.scope) == "allow":
            return context.user_id
        payer = await self.group_payer(context.chat_id)
        return context.user_id if payer is None else payer

    async def payer_qualifies(self, user_id: int) -> bool:
        if self.is_owner(user_id):
            return True
        if await self.database.access_effect(Scope("user", user_id)) == "allow":
            return True
        entitlement = await self.database.active_entitlement(user_id)
        return entitlement is not None and entitlement.plan in {"trial", "plus"}

    async def group_payer(self, chat_id: int) -> int | None:
        return await self._resolve_group_payer(chat_id, refresh=False)

    async def _resolve_group_payer(self, chat_id: int, *, refresh: bool) -> int | None:
        used_cache = not refresh and self._cache_valid(chat_id)
        admins = await self._admins(chat_id, refresh=refresh)
        stored = await self.database.group_plus_payer(chat_id)
        if admins is None:
            if stored is not None and await self.payer_qualifies(stored):
                return stored
            return None
        payer = await self._select_payer(admins, stored)
        if payer != stored:
            await self.database.set_group_plus_payer(chat_id, payer)
        if payer is None and used_cache:
            return await self._resolve_group_payer(chat_id, refresh=True)
        return payer

    async def _select_payer(
        self, admins: Sequence[ChatAdministrator], stored: int | None
    ) -> int | None:
        humans = tuple(item for item in admins if not item.is_bot)
        present = {item.user_id for item in humans}
        if stored is not None and stored in present and await self.payer_qualifies(stored):
            return stored
        creator = next((item for item in humans if item.is_creator), None)
        if creator is not None and await self.payer_qualifies(creator.user_id):
            return creator.user_id
        for item in humans:
            if creator is not None and item.user_id == creator.user_id:
                continue
            if await self.payer_qualifies(item.user_id):
                return item.user_id
        return None

    def _cache_valid(self, chat_id: int) -> bool:
        cached = self._admin_cache.get(chat_id)
        if cached is None:
            return False
        return (time.monotonic() - cached[0]) < self._admin_cache_ttl

    async def _admins(self, chat_id: int, *, refresh: bool) -> tuple[ChatAdministrator, ...] | None:
        cached = self._admin_cache.get(chat_id)
        if not refresh and self._cache_valid(chat_id) and cached is not None:
            return cached[1]
        if self._list_administrators is None:
            return None
        fetched = await self._list_administrators(chat_id)
        if fetched is None:
            return cached[1] if cached is not None else None
        admins = tuple(fetched)
        self._admin_cache[chat_id] = (time.monotonic(), admins)
        return admins
