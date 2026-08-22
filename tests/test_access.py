from pathlib import Path

import pytest

from skye.access import AccessService
from skye.db import Database
from skye.models import RequestContext, Scope


@pytest.fixture
async def database(tmp_path: Path):
    value = Database(tmp_path / "skye.db", "gpt-5.6-luna", "medium")
    await value.open()
    try:
        yield value
    finally:
        await value.close()


async def _plus_entitlement(database: Database, user_id: int) -> None:
    await database.upsert_star_entitlement(
        user_id=user_id,
        plan="plus",
        auto_renew=True,
        expires_at=9_999_999_999,
        telegram_payment_charge_id=f"chg-{user_id}",
        trial_used=False,
    )


async def test_private_free_user_can_chat(database: Database) -> None:
    access = AccessService(database, frozenset({1}))
    private = RequestContext(42, "private", user_id=42)

    assert await access.allowed(private)
    assert not await access.plus(private)


async def test_ban_blocks_private_free_and_plus_users(database: Database) -> None:
    access = AccessService(database, frozenset({1}))
    await _plus_entitlement(database, 42)
    await database.set_access(Scope("user", 42), "ban", created_by=1)
    await database.set_access(Scope("user", 7), "ban", created_by=1)
    banned_plus = RequestContext(42, "private", user_id=42)
    banned_free = RequestContext(7, "private", user_id=7)
    owner = RequestContext(1, "private", user_id=1)

    assert not await access.allowed(banned_plus)
    assert not await access.plus(banned_plus)
    assert not await access.allowed(banned_free)
    assert await access.allowed(owner)
    assert await access.plus(owner)


async def test_groups_stay_allowlist_only(database: Database) -> None:
    access = AccessService(database, frozenset({1}))
    await _plus_entitlement(database, 42)
    group = RequestContext(-100, "supergroup", user_id=42)
    other_group = RequestContext(-200, "supergroup", user_id=42)
    private = RequestContext(42, "private", user_id=42)

    assert not await access.allowed(group)
    assert not await access.plus(group)
    await database.set_access(Scope("chat", -100), "allow", created_by=1)
    assert await access.allowed(group)
    assert await access.plus(group)
    assert not await access.allowed(other_group)
    assert await access.allowed(private)


async def test_agent_authoring_requires_plus_complimentary_or_owner(database: Database) -> None:
    access = AccessService(database, frozenset({1}))
    free = RequestContext(42, "private", user_id=42)
    plus_user = RequestContext(9, "private", user_id=9)
    complimentary = RequestContext(8, "private", user_id=8)
    owner = RequestContext(1, "private", user_id=1)
    await _plus_entitlement(database, 9)
    await database.set_access(Scope("user", 8), "allow", created_by=1)

    assert not await access.plus(free)
    assert await access.plus(plus_user)
    assert await access.plus(complimentary)
    assert await access.plus(owner)
