from __future__ import annotations

from datetime import datetime

from .access import AccessService
from .billing import BillingService
from .db import Database
from .models import RequestContext, Scope

FREE_DAILY = 20_000
FREE_MONTHLY = 400_000
PLUS_DAILY = 250_000
PLUS_MONTHLY = 6_000_000

DAILY_LIMIT_COPY = "The daily message allowance is used. You can continue tomorrow."
MONTHLY_LIMIT_COPY = (
    "The monthly message allowance is used. You can continue when the next period starts."
)


class AllowanceError(Exception):
    """The user is already over the message allowance for this period."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class QuotaService:
    def __init__(self, database: Database, billing: BillingService, access: AccessService) -> None:
        self.database = database
        self.billing = billing
        self.access = access

    async def complimentary(
        self, context: RequestContext, *, billed_user_id: int | None = None
    ) -> bool:
        user_id = self._billed_user_id(context, billed_user_id)
        if self.access.is_owner(user_id):
            return True
        if await self.database.access_effect(Scope("user", user_id)) == "allow":
            return True
        return await self.database.access_effect(context.scope) == "allow"

    async def limits(
        self, context: RequestContext, *, billed_user_id: int | None = None
    ) -> tuple[int, int]:
        entitlement = await self.billing.entitlement(self._billed_user_id(context, billed_user_id))
        if entitlement is not None and entitlement.plan == "plus":
            return PLUS_DAILY, PLUS_MONTHLY
        return FREE_DAILY, FREE_MONTHLY

    async def check(
        self,
        context: RequestContext,
        *,
        billed_user_id: int | None = None,
        now: datetime | None = None,
    ) -> None:
        user_id = self._billed_user_id(context, billed_user_id)
        if await self.complimentary(context, billed_user_id=user_id):
            return
        daily_limit, monthly_limit = await self.limits(context, billed_user_id=user_id)
        daily, monthly = await self.database.usage_totals(user_id, now=now)
        if monthly >= monthly_limit:
            raise AllowanceError(MONTHLY_LIMIT_COPY)
        if daily >= daily_limit:
            raise AllowanceError(DAILY_LIMIT_COPY)

    async def record(
        self,
        context: RequestContext,
        tokens: int,
        *,
        billed_user_id: int | None = None,
        now: datetime | None = None,
    ) -> None:
        if tokens <= 0:
            return
        await self.database.add_usage(
            self._billed_user_id(context, billed_user_id), tokens, now=now
        )

    @staticmethod
    def _billed_user_id(context: RequestContext, billed_user_id: int | None) -> int:
        return context.user_id if billed_user_id is None else billed_user_id
