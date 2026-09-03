import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram.types import (
    InlineKeyboardButton,
    SuccessfulPayment,
)

from skye.access import AccessService
from skye.billing import (
    PLANS,
    SUBSCRIPTION_PERIOD,
    AccountPanel,
    BillingError,
    BillingService,
    decode_payload,
    encode_payload,
    remaining_copy,
)
from skye.db import Database
from skye.models import RequestContext, Scope, StarEntitlement
from skye.quota import (
    DAILY_LIMIT_COPY,
    FREE_DAILY,
    MONTHLY_LIMIT_COPY,
    PLUS_DAILY,
    AllowanceError,
    QuotaService,
)
from skye.rich import RichMessages


@pytest.fixture
async def database(tmp_path: Path):
    value = Database(tmp_path / "skye.db", "gpt-5.6-luna", "medium")
    await value.open()
    try:
        yield value
    finally:
        await value.close()


def payment(
    service: BillingService,
    plan_id: str,
    user_id: int,
    *,
    charge_id: str = "tg_charge_1",
    recurring: bool = False,
    first: bool = False,
    expires: int | None = None,
    stars: int | None = None,
) -> SuccessfulPayment:
    plan = PLANS[plan_id]
    return SuccessfulPayment(
        currency="XTR",
        total_amount=stars if stars is not None else plan.stars,
        invoice_payload=service.payload(plan, user_id),
        telegram_payment_charge_id=charge_id,
        provider_payment_charge_id="",
        subscription_expiration_date=expires,
        is_recurring=True if recurring else None,
        is_first_recurring=True if first else None,
    )


def test_plan_catalog_matches_the_product() -> None:
    assert list(PLANS) == ["plus"]
    assert PLANS["plus"].stars == 449
    assert PLANS["plus"].recurring is True
    assert PLANS["plus"].button_label.startswith("🌙")
    assert all(len(plan.invoice_title) <= 32 for plan in PLANS.values())
    assert all(len(plan.invoice_description) <= 255 for plan in PLANS.values())
    joined = " ".join(
        f"{plan.invoice_title} {plan.invoice_description} {plan.button_label}"
        for plan in PLANS.values()
    )
    for banned in ("Luna", "Terra", "Sol", "GPT", "token", "Try Skye", "Super", "Ultra"):
        assert banned not in joined


def test_invoice_payload_is_signed_and_bound_to_the_user() -> None:
    payload = encode_payload("plus", 42, "secret")
    plan, user_id = decode_payload(payload, "secret")

    assert plan.id == "plus"
    assert user_id == 42
    with pytest.raises(BillingError):
        decode_payload(payload, "other")
    with pytest.raises(BillingError):
        decode_payload("plus:42:deadbeefdeadbeef", "secret")


def test_remaining_copy_explains_renewal_and_a_hard_end() -> None:
    now = 1_000_000
    active = StarEntitlement(1, "plus", True, now + 18 * 86_400, "chg", False, "now", "now")
    ending = StarEntitlement(1, "plus", False, now + 3 * 86_400, "chg", False, "now", "now")

    renew = remaining_copy(active, now)
    stop = remaining_copy(ending, now)

    assert "18 days left" in renew
    assert "Renews automatically" in renew
    assert "3 days left" in stop
    assert "no further charges" in stop


async def test_plus_does_not_grant_group_access(database: Database) -> None:
    access = AccessService(database, frozenset({1}))
    billing = BillingService(database, "secret")
    now = int(time.time())
    await billing.apply_payment(
        42,
        payment(
            billing,
            "plus",
            42,
            recurring=True,
            first=True,
            expires=now + SUBSCRIPTION_PERIOD,
        ),
    )

    private = RequestContext(42, "private", user_id=42)
    group = RequestContext(-100, "supergroup", user_id=42)
    other = RequestContext(99, "private", user_id=99)

    assert await access.allowed(private)
    assert await access.plus(private)
    assert not await access.allowed(group)
    assert not await access.plus(group)
    assert await access.allowed(other)
    assert not await access.plus(other)


async def test_ban_beats_an_active_subscription(database: Database) -> None:
    access = AccessService(database, frozenset({1}))
    billing = BillingService(database, "secret")
    now = int(time.time())
    await billing.apply_payment(
        42,
        payment(billing, "plus", 42, recurring=True, first=True, expires=now + 1_000),
    )
    await database.set_access(Scope("user", 42), "ban", created_by=1)

    assert not await access.allowed(RequestContext(42, "private", user_id=42))
    assert await access.allowed(RequestContext(1, "private", user_id=1))


async def test_legacy_plans_cannot_be_purchased(database: Database) -> None:
    billing = BillingService(database, "secret")
    payload = encode_payload("trial", 7, "secret")
    with pytest.raises(BillingError, match="Unknown"):
        billing.validate_checkout(
            user_id=7,
            currency="XTR",
            total_amount=49,
            invoice_payload=payload,
            entitlement=None,
            now=int(time.time()),
        )


async def test_legacy_renewal_keeps_access(database: Database) -> None:
    billing = BillingService(database, "secret")
    now = int(time.time())
    await database.upsert_star_entitlement(
        user_id=9,
        plan="super",
        auto_renew=True,
        expires_at=now + 1_000,
        telegram_payment_charge_id="legacy",
        trial_used=False,
    )
    payload = encode_payload("super", 9, "secret")
    plan = billing.validate_checkout(
        user_id=9,
        currency="XTR",
        total_amount=1_199,
        invoice_payload=payload,
        entitlement=await database.star_entitlement(9),
        now=now,
        renewal=True,
    )
    assert plan.id == "super"
    access = AccessService(database, frozenset({1}))
    assert await access.allowed(RequestContext(9, "private", user_id=9))


async def test_recurring_payment_extends_expiry_without_changing_charge_id(
    database: Database,
) -> None:
    billing = BillingService(database, "secret")
    now = int(time.time())
    first = await billing.apply_payment(
        5,
        payment(
            billing,
            "plus",
            5,
            charge_id="first",
            recurring=True,
            first=True,
            expires=now + SUBSCRIPTION_PERIOD,
        ),
    )
    renewed = await billing.apply_payment(
        5,
        payment(
            billing,
            "plus",
            5,
            charge_id="second",
            recurring=True,
            first=False,
            expires=now + 2 * SUBSCRIPTION_PERIOD,
        ),
    )

    assert first.telegram_payment_charge_id == "first"
    assert renewed.telegram_payment_charge_id == "first"
    assert renewed.expires_at == now + 2 * SUBSCRIPTION_PERIOD
    assert renewed.auto_renew is True


async def test_duplicate_charge_id_is_idempotent(database: Database) -> None:
    billing = BillingService(database, "secret")
    now = int(time.time())
    item = payment(
        billing, "plus", 8, charge_id="same", recurring=True, first=True, expires=now + 100
    )
    first = await billing.apply_payment(8, item)
    again = await billing.apply_payment(8, item)
    assert first == again


async def test_unknown_settings_are_clamped_to_the_hosted_model(database: Database) -> None:
    billing = BillingService(database, "secret")
    access = AccessService(database, frozenset({1}))
    now = int(time.time())
    await database.set_model(Scope("user", 42), "gpt-5.6-sol")
    await billing.apply_payment(
        42,
        payment(billing, "plus", 42, recurring=True, first=True, expires=now + 1_000),
    )
    settings = await database.get_settings(Scope("user", 42))
    clamped = await billing.clamp_settings(
        RequestContext(42, "private", user_id=42), settings, access
    )
    assert settings.model == "gpt-5.6-luna"
    assert clamped.model == "gpt-5.6-luna"


async def test_cancel_renewal_keeps_access_until_expiry(database: Database) -> None:
    billing = BillingService(database, "secret")
    access = AccessService(database, frozenset({1}))
    now = int(time.time())
    await billing.apply_payment(
        42,
        payment(
            billing,
            "plus",
            42,
            charge_id="sub",
            recurring=True,
            first=True,
            expires=now + SUBSCRIPTION_PERIOD,
        ),
    )
    bot = AsyncMock()
    canceled = await billing.cancel_renewal(42, bot)

    bot.edit_user_star_subscription.assert_awaited_once_with(
        user_id=42, telegram_payment_charge_id="sub", is_canceled=True
    )
    assert canceled.auto_renew is False
    assert await access.allowed(RequestContext(42, "private", user_id=42))
    copy = remaining_copy(canceled, now)
    assert "no further charges" in copy


def test_account_buttons_offer_plus_or_cancel_only() -> None:
    markup = AccountPanel._home_keyboard(None, owner=False)
    assert markup is not None
    labels = [button.text for row in markup.inline_keyboard for button in row]
    callbacks = [button.callback_data or "" for row in markup.inline_keyboard for button in row]
    assert labels == [PLANS["plus"].button_label]
    assert callbacks == ["acct:plan:plus"]
    assert all(item.startswith("acct:") and len(item) <= 64 for item in callbacks)
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert all(isinstance(button, InlineKeyboardButton) for button in buttons)

    active = StarEntitlement(1, "plus", True, 9_999_999_999, "chg", False, "now", "now")
    plus_home = AccountPanel._home_keyboard(active, owner=False)
    assert plus_home is not None
    plus_labels = [button.text for row in plus_home.inline_keyboard for button in row]
    assert plus_labels == ["Cancel renewal"]


def test_checkout_explains_allowance_without_model_names() -> None:
    from aiogram.types import InputRichBlockDetails

    message = RichMessages.plan_checkout(
        name="Skye Plus",
        emoji="🌙",
        stars=449,
        recurring=True,
    )
    assert message.blocks
    details = message.blocks[-1]
    assert isinstance(details, InputRichBlockDetails)
    assert details.summary == "Plans"
    blob = " ".join(str(getattr(block, "text", "")) for block in message.blocks)
    blob += " " + " ".join(str(getattr(block, "text", "")) for block in details.blocks)
    assert "449" in blob
    assert "more room for longer work" in blob.lower()
    for banned in ("Luna", "Terra", "Sol", "GPT", "token", "Fair Use", "Try Skye"):
        assert banned not in blob


async def test_quota_blocks_before_recording(database: Database) -> None:
    billing = BillingService(database, "secret")
    access = AccessService(database, frozenset({1}))
    quota = QuotaService(database, billing, access)
    context = RequestContext(42, "private", user_id=42)
    now = datetime(2026, 8, 22, tzinfo=UTC)

    await quota.check(context, now=now)
    await quota.record(context, FREE_DAILY, now=now)
    with pytest.raises(AllowanceError, match="daily message allowance") as error:
        await quota.check(context, now=now)
    assert str(error.value) == DAILY_LIMIT_COPY
    assert "20" not in str(error.value)
    assert "token" not in str(error.value).lower()


async def test_plus_quota_uses_the_higher_allowance(database: Database) -> None:
    billing = BillingService(database, "secret")
    access = AccessService(database, frozenset({1}))
    quota = QuotaService(database, billing, access)
    now_ts = int(time.time())
    await billing.apply_payment(
        42,
        payment(billing, "plus", 42, recurring=True, first=True, expires=now_ts + 1_000),
    )
    context = RequestContext(42, "private", user_id=42)
    now = datetime(2026, 8, 22, tzinfo=UTC)
    await quota.record(context, FREE_DAILY, now=now)
    await quota.check(context, now=now)
    await quota.record(context, PLUS_DAILY - FREE_DAILY, now=now)
    with pytest.raises(AllowanceError, match="daily message allowance"):
        await quota.check(context, now=now)


async def test_monthly_copy_is_used_when_the_month_is_the_one_that_hit(
    database: Database,
) -> None:
    billing = BillingService(database, "secret")
    access = AccessService(database, frozenset({1}))
    quota = QuotaService(database, billing, access)
    context = RequestContext(7, "private", user_id=7)
    now = datetime(2026, 8, 22, tzinfo=UTC)
    await quota.record(context, 400_000, now=now)
    with pytest.raises(AllowanceError, match="monthly message allowance") as error:
        await quota.check(context, now=now)
    assert str(error.value) == MONTHLY_LIMIT_COPY


async def test_owner_and_allowlist_bypass_quota(database: Database) -> None:
    billing = BillingService(database, "secret")
    access = AccessService(database, frozenset({1}))
    quota = QuotaService(database, billing, access)
    now = datetime(2026, 8, 22, tzinfo=UTC)
    owner = RequestContext(1, "private", user_id=1)
    allowed = RequestContext(42, "private", user_id=42)
    await database.set_access(Scope("user", 42), "allow", created_by=1)
    await quota.record(owner, 9_000_000, now=now)
    await quota.record(allowed, 9_000_000, now=now)
    await quota.check(owner, now=now)
    await quota.check(allowed, now=now)


async def test_group_usage_increments_payer_not_speaker(database: Database) -> None:
    billing = BillingService(database, "secret")
    access = AccessService(database, frozenset({1}))
    quota = QuotaService(database, billing, access)
    now_ts = int(time.time())
    await billing.apply_payment(
        5,
        payment(billing, "plus", 5, recurring=True, first=True, expires=now_ts + 1_000),
    )
    speaker = RequestContext(-100, "supergroup", user_id=42)
    now = datetime(2026, 8, 22, tzinfo=UTC)

    await quota.record(speaker, 1_500, billed_user_id=5, now=now)
    assert await database.usage_totals(5, now=now) == (1_500, 1_500)
    assert await database.usage_totals(42, now=now) == (0, 0)


async def test_group_waits_on_the_payer_allowance(database: Database) -> None:
    billing = BillingService(database, "secret")
    access = AccessService(database, frozenset({1}))
    quota = QuotaService(database, billing, access)
    now_ts = int(time.time())
    await billing.apply_payment(
        5,
        payment(billing, "plus", 5, recurring=True, first=True, expires=now_ts + 1_000),
    )
    speaker = RequestContext(-100, "supergroup", user_id=42)
    now = datetime(2026, 8, 22, tzinfo=UTC)

    await quota.record(speaker, FREE_DAILY, billed_user_id=5, now=now)
    await quota.check(speaker, billed_user_id=5, now=now)
    await quota.record(speaker, PLUS_DAILY - FREE_DAILY, billed_user_id=5, now=now)
    with pytest.raises(AllowanceError, match="daily message allowance") as error:
        await quota.check(speaker, billed_user_id=5, now=now)
    assert str(error.value) == DAILY_LIMIT_COPY
    assert "token" not in str(error.value).lower()
    assert await database.usage_totals(42, now=now) == (0, 0)


async def test_group_allowlist_still_bypasses_quota(database: Database) -> None:
    billing = BillingService(database, "secret")
    access = AccessService(database, frozenset({1}))
    quota = QuotaService(database, billing, access)
    await database.set_access(Scope("chat", -100), "allow", created_by=1)
    speaker = RequestContext(-100, "supergroup", user_id=42)
    now = datetime(2026, 8, 22, tzinfo=UTC)
    await quota.record(speaker, 9_000_000, now=now)
    await quota.check(speaker, now=now)


async def test_usage_resets_on_a_new_utc_day(database: Database) -> None:
    first = datetime(2026, 8, 22, 23, 0, tzinfo=UTC)
    later = datetime(2026, 8, 23, 0, 1, tzinfo=UTC)
    await database.add_usage(42, FREE_DAILY, now=first)
    daily, monthly = await database.usage_totals(42, now=later)
    assert daily == 0
    assert monthly == FREE_DAILY


async def test_expired_unknown_plan_is_treated_as_free(database: Database) -> None:
    billing = BillingService(database, "secret")
    access = AccessService(database, frozenset({1}))
    quota = QuotaService(database, billing, access)
    now_ts = int(time.time())
    await database.upsert_star_entitlement(
        user_id=11,
        plan="ultra",
        auto_renew=False,
        expires_at=now_ts - 10,
        telegram_payment_charge_id="old",
        trial_used=False,
    )
    context = RequestContext(11, "private", user_id=11)
    daily, monthly = await quota.limits(context)
    assert (daily, monthly) == (20_000, 400_000)
    assert await billing.entitlement(11, now=now_ts) is None
