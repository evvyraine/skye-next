from __future__ import annotations

from .db import Database
from .models import RequestContext, Scope


class AccessService:
    def __init__(self, database: Database, owner_ids: frozenset[int]) -> None:
        self.database = database
        self.owner_ids = owner_ids

    def is_owner(self, user_id: int) -> bool:
        return user_id in self.owner_ids

    async def allowed(self, context: RequestContext) -> bool:
        if self.is_owner(context.user_id):
            return True
        if await self.database.access_effect(Scope("user", context.user_id)) == "ban":
            return False
        if context.chat_type == "private":
            return True
        return await self.database.access_effect(context.scope) == "allow"

    async def plus(self, context: RequestContext) -> bool:
        if self.is_owner(context.user_id):
            return True
        if await self.database.access_effect(Scope("user", context.user_id)) == "ban":
            return False
        if await self.database.access_effect(context.scope) == "allow":
            return True
        entitlement = await self.database.active_entitlement(context.user_id)
        return entitlement is not None and entitlement.plan == "plus"
