from datetime import UTC, datetime
from pathlib import Path

import pytest

from skye.access import AccessService
from skye.billing import BillingService
from skye.db import Database
from skye.growth import TRIAL_SECONDS, GrowthService
from skye.models import RequestContext
from skye.quota import FREE_DAILY, QuotaService


@pytest.fixture
async def database(tmp_path: Path):
    value = Database(tmp_path / "skye.db", "gpt-5.6-luna", "medium")
    await value.open()
    try:
        yield value
    finally:
        await value.close()


def test_sources_are_short_opaque_labels() -> None:
    assert GrowthService.source("src_launch") == "launch"
    assert GrowthService.source("ref_friend-42") == "friend-42"
    assert GrowthService.source("agent_shared") is None
    assert GrowthService.source("src_not allowed") is None


async def test_trial_starts_after_activation_and_only_once(database: Database) -> None:
    billing = BillingService(database, "secret")
    growth = GrowthService(database)
    first_day = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())
    second_day = int(datetime(2026, 9, 2, tzinfo=UTC).timestamp())

    assert not await growth.completed_task(42, "chat", occurred_at=first_day)
    assert not await growth.completed_task(42, "document", occurred_at=first_day + 60)
    assert await growth.completed_task(42, "chat", occurred_at=second_day)

    progress = await growth.progress(42)
    assert progress.activated
    entitlement = await billing.entitlement(42, now=second_day)
    assert entitlement is not None
    assert entitlement.plan == "trial"
    assert entitlement.expires_at == second_day + TRIAL_SECONDS
    assert entitlement.trial_used

    await database.expire_star_entitlement(42, second_day + TRIAL_SECONDS)
    assert not await growth.completed_task(
        42, "tool", occurred_at=second_day + TRIAL_SECONDS + 1
    )


async def test_trial_receives_plus_allowance(database: Database) -> None:
    billing = BillingService(database, "secret")
    access = AccessService(database, frozenset())
    quota = QuotaService(database, billing, access)
    growth = GrowthService(database)
    day_one = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())
    day_two = int(datetime(2026, 9, 2, tzinfo=UTC).timestamp())
    await growth.completed_task(7, "document", occurred_at=day_one)
    await growth.completed_task(7, "chat", occurred_at=day_one + 1)
    await growth.completed_task(7, "chat", occurred_at=day_two)

    context = RequestContext(7, "private", user_id=7)
    now = datetime(2026, 9, 2, tzinfo=UTC)
    await quota.record(context, FREE_DAILY, now=now)
    await quota.check(context, now=now)
