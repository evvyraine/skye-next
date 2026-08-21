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
        if await self.database.access_effect(context.scope) == "allow":
            return True
        if context.chat_type == "private":
            return await self.database.active_entitlement(context.user_id) is not None
        return False
