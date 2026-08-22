from collections.abc import Sequence
from pathlib import Path

import pytest

from skye.access import AccessService, ChatAdministrator
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


def _lister(
    *admins: ChatAdministrator,
    fail: bool = False,
    calls: list[int] | None = None,
    live: list[ChatAdministrator] | None = None,
):
    async def list_administrators(chat_id: int) -> Sequence[ChatAdministrator] | None:
        if calls is not None:
            calls.append(chat_id)
        if fail:
            return None
        if live is not None:
            return tuple(live)
        return admins

    return list_administrators


def _access(
    database: Database,
    *admins: ChatAdministrator,
    owner_ids: frozenset[int] = frozenset({1}),
    admin_cache_ttl: float = 90.0,
    fail: bool = False,
    calls: list[int] | None = None,
    live: list[ChatAdministrator] | None = None,
) -> AccessService:
    return AccessService(
        database,
        owner_ids,
        list_administrators=_lister(*admins, fail=fail, calls=calls, live=live),
        admin_cache_ttl=admin_cache_ttl,
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


async def test_groups_stay_allowlist_only_without_plus_admin(database: Database) -> None:
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


async def test_group_denied_without_plus_admin(database: Database) -> None:
    await _plus_entitlement(database, 42)
    access = _access(
        database,
        ChatAdministrator(5, True),
        ChatAdministrator(9, False),
    )
    group = RequestContext(-100, "supergroup", user_id=42)

    assert not await access.allowed(group)
    assert await access.group_payer(-100) is None
    assert await access.allowed(RequestContext(42, "private", user_id=42))


async def test_group_allowed_when_owner_has_plus(database: Database) -> None:
    await _plus_entitlement(database, 5)
    access = _access(
        database,
        ChatAdministrator(5, True),
        ChatAdministrator(9, False),
    )
    speaker = RequestContext(-100, "supergroup", user_id=42)

    assert await access.allowed(speaker)
    assert await access.group_payer(-100) == 5
    assert await access.billed_user_id(speaker) == 5
    assert not await access.plus(speaker)


async def test_free_speaker_is_allowed_in_plus_admin_group(database: Database) -> None:
    await _plus_entitlement(database, 9)
    access = _access(
        database,
        ChatAdministrator(5, True),
        ChatAdministrator(9, False),
    )
    free = RequestContext(-100, "group", user_id=42)
    forum = RequestContext(-100, "supergroup", user_id=42, thread_id=17)

    assert await access.allowed(free)
    assert await access.allowed(forum)
    assert await access.group_payer(-100) == 9
    assert await access.billed_user_id(free) == 9
    assert await access.billed_user_id(forum) == 9


async def test_free_admin_does_not_unlock_a_group(database: Database) -> None:
    access = _access(
        database,
        ChatAdministrator(42, True),
        ChatAdministrator(9, False),
    )
    assert not await access.allowed(RequestContext(-100, "supergroup", user_id=42))
    assert not await access.allowed(RequestContext(-100, "supergroup", user_id=7))


async def test_sticky_payer_is_reused_while_they_qualify(database: Database) -> None:
    await _plus_entitlement(database, 10)
    access = _access(
        database,
        ChatAdministrator(5, True),
        ChatAdministrator(10, False),
        ChatAdministrator(20, False),
    )
    speaker = RequestContext(-100, "supergroup", user_id=42)

    assert await access.group_payer(-100) == 10
    await _plus_entitlement(database, 5)
    assert await access.group_payer(-100) == 10
    assert await access.billed_user_id(speaker) == 10


async def test_payer_is_re_resolved_when_they_lapse(database: Database) -> None:
    await _plus_entitlement(database, 10)
    await _plus_entitlement(database, 20)
    access = _access(
        database,
        ChatAdministrator(5, True),
        ChatAdministrator(10, False),
        ChatAdministrator(20, False),
    )

    assert await access.group_payer(-100) == 10
    await database.expire_star_entitlement(10, 1)
    assert await access.group_payer(-100) == 20


async def test_payer_is_re_resolved_when_they_leave_admins(database: Database) -> None:
    await _plus_entitlement(database, 10)
    await _plus_entitlement(database, 20)
    live = [
        ChatAdministrator(5, True),
        ChatAdministrator(10, False),
        ChatAdministrator(20, False),
    ]
    access = _access(database, live=live, admin_cache_ttl=0)

    assert await access.group_payer(-100) == 10
    live.remove(live[1])
    assert await access.group_payer(-100) == 20


async def test_group_allowlist_still_grants_access(database: Database) -> None:
    access = AccessService(database, frozenset({1}))
    await database.set_access(Scope("chat", -100), "allow", created_by=1)
    speaker = RequestContext(-100, "supergroup", user_id=42)

    assert await access.allowed(speaker)
    assert await access.billed_user_id(speaker) == 42
    assert await access.plus(speaker)


async def test_user_ban_blocks_plus_admin_group(database: Database) -> None:
    await _plus_entitlement(database, 5)
    access = _access(database, ChatAdministrator(5, True))
    await database.set_access(Scope("user", 42), "ban", created_by=1)

    assert not await access.allowed(RequestContext(-100, "supergroup", user_id=42))
    assert await access.allowed(RequestContext(-100, "supergroup", user_id=7))
    assert await access.allowed(RequestContext(-100, "supergroup", user_id=1))


async def test_complimentary_creator_unlocks_the_group(database: Database) -> None:
    access = _access(
        database,
        ChatAdministrator(1, True),
        ChatAdministrator(9, False),
    )
    speaker = RequestContext(-100, "supergroup", user_id=42)

    assert await access.allowed(speaker)
    assert await access.group_payer(-100) == 1
    assert await access.billed_user_id(speaker) == 1


async def test_user_allowlist_admin_unlocks_the_group(database: Database) -> None:
    await database.set_access(Scope("user", 9), "allow", created_by=1)
    access = _access(
        database,
        ChatAdministrator(5, True),
        ChatAdministrator(9, False),
    )
    speaker = RequestContext(-100, "supergroup", user_id=42)

    assert await access.allowed(speaker)
    assert await access.group_payer(-100) == 9


async def test_admin_cache_avoids_repeat_telegram_calls(database: Database) -> None:
    await _plus_entitlement(database, 5)
    calls: list[int] = []
    access = _access(
        database,
        ChatAdministrator(5, True),
        calls=calls,
    )
    group = RequestContext(-100, "supergroup", user_id=42)

    assert await access.allowed(group)
    assert await access.allowed(group)
    assert calls == [-100]


async def test_stale_admin_cache_refreshes_when_nobody_qualifies(
    database: Database,
) -> None:
    live = [ChatAdministrator(5, True)]
    calls: list[int] = []
    access = _access(database, live=live, calls=calls)
    group = RequestContext(-100, "supergroup", user_id=42)

    assert not await access.allowed(group)
    assert calls == [-100]
    live.append(ChatAdministrator(9, False))
    await _plus_entitlement(database, 9)
    assert await access.allowed(group)
    assert calls == [-100, -100]
    assert await access.group_payer(-100) == 9


async def test_stored_payer_survives_telegram_failure(database: Database) -> None:
    await _plus_entitlement(database, 5)
    access = _access(database, ChatAdministrator(5, True))
    assert await access.group_payer(-100) == 5

    failing = _access(database, fail=True)
    speaker = RequestContext(-100, "supergroup", user_id=42)
    assert await failing.allowed(speaker)
    assert await failing.group_payer(-100) == 5


async def test_bots_are_not_selected_as_payers(database: Database) -> None:
    await _plus_entitlement(database, 777)
    await _plus_entitlement(database, 9)
    access = _access(
        database,
        ChatAdministrator(5, True),
        ChatAdministrator(777, False, is_bot=True),
        ChatAdministrator(9, False),
    )

    assert await access.group_payer(-100) == 9
