from __future__ import annotations

from datetime import datetime

from .access import AccessService
from .billing import BillingService
from .db import Database
from .models import RequestContext

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
    def __init__(
        self, database: Database, billing: BillingService, access: AccessService
    ) -> None:
        self.database = database
        self.billing = billing
        self.access = access

    async def complimentary(self, context: RequestContext) -> bool:
        return await self.billing.complimentary(context, self.access)

    async def limits(self, context: RequestContext) -> tuple[int, int]:
        entitlement = await self.billing.entitlement(context.user_id)
        if entitlement is not None and entitlement.plan == "plus":
            return PLUS_DAILY, PLUS_MONTHLY
        return FREE_DAILY, FREE_MONTHLY

    async def check(self, context: RequestContext, *, now: datetime | None = None) -> None:
        if await self.complimentary(context):
            return
        daily_limit, monthly_limit = await self.limits(context)
        daily, monthly = await self.database.usage_totals(context.user_id, now=now)
        if monthly >= monthly_limit:
            raise AllowanceError(MONTHLY_LIMIT_COPY)
        if daily >= daily_limit:
            raise AllowanceError(DAILY_LIMIT_COPY)

    async def record(
        self, context: RequestContext, tokens: int, *, now: datetime | None = None
    ) -> None:
        if tokens <= 0:
            return
        await self.database.add_usage(context.user_id, tokens, now=now)
