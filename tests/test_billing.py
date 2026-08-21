import time
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
) -> SuccessfulPayment:
    plan = PLANS[plan_id]
    return SuccessfulPayment(
        currency="XTR",
        total_amount=plan.stars,
        invoice_payload=service.payload(plan, user_id),
        telegram_payment_charge_id=charge_id,
        provider_payment_charge_id="",
        subscription_expiration_date=expires,
        is_recurring=True if recurring else None,
        is_first_recurring=True if first else None,
    )


def test_plan_catalog_matches_the_product() -> None:
    assert PLANS["trial"].stars == 49
    assert PLANS["plus"].stars == 499
    assert PLANS["super"].stars == 1_199
    assert PLANS["ultra"].stars == 2_599
    assert PLANS["plus"].models == ("gpt-5.6-luna",)
    assert PLANS["super"].models == ("gpt-5.6-luna", "gpt-5.6-terra")
    assert PLANS["ultra"].models[-1] == "gpt-5.6-sol"
    assert all(plan.recurring is (plan.id != "trial") for plan in PLANS.values())
    assert all(len(plan.invoice_title) <= 32 for plan in PLANS.values())
    assert all(len(plan.invoice_description) <= 255 for plan in PLANS.values())
    assert PLANS["plus"].button_label.startswith("🌙")
    assert PLANS["super"].button_label.startswith("🌍")
    assert PLANS["ultra"].button_label.startswith("☀️")
    assert PLANS["trial"].button_label.startswith("✨")


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


async def test_subscription_grants_private_access_not_group_access(database: Database) -> None:
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
    assert not await access.allowed(group)
    assert not await access.allowed(other)


async def test_ban_beats_an_active_subscription(database: Database) -> None:
    access = AccessService(database, frozenset({1}))
    billing = BillingService(database, "secret")
    now = int(time.time())
    await billing.apply_payment(
        42,
        payment(billing, "super", 42, recurring=True, first=True, expires=now + 1_000),
    )
    await database.set_access(Scope("user", 42), "ban", created_by=1)

    assert not await access.allowed(RequestContext(42, "private", user_id=42))
    assert await access.allowed(RequestContext(1, "private", user_id=1))


async def test_trial_cannot_be_used_twice(database: Database) -> None:
    billing = BillingService(database, "secret")
    first = await billing.apply_payment(7, payment(billing, "trial", 7, charge_id="trial-1"))
    later = int(time.time()) + 8 * 86_400
    await database.expire_star_entitlement(7, later)

    assert first.plan == "trial"
    assert first.auto_renew is False
    assert await billing.trial_used(7)
    with pytest.raises(BillingError, match="once"):
        billing.validate_checkout(
            user_id=7,
            currency="XTR",
            total_amount=49,
            invoice_payload=billing.payload(PLANS["trial"], 7),
            entitlement=await database.star_entitlement(7),
            trial_used=True,
            now=later,
        )


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
        billing, "ultra", 8, charge_id="same", recurring=True, first=True, expires=now + 100
    )
    first = await billing.apply_payment(8, item)
    again = await billing.apply_payment(8, item)
    assert first == again


async def test_plus_clamps_sol_down_to_luna(database: Database) -> None:
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


def test_account_buttons_use_plan_emoji_and_short_callback_data() -> None:
    markup = AccountPanel._home_keyboard(None, trial_used=False, owner=False)
    assert markup is not None
    labels = [button.text for row in markup.inline_keyboard for button in row]
    callbacks = [button.callback_data or "" for row in markup.inline_keyboard for button in row]
    assert labels == [
        PLANS["trial"].button_label,
        PLANS["plus"].button_label,
        PLANS["super"].button_label,
        PLANS["ultra"].button_label,
    ]
    assert all(item.startswith("acct:") and len(item) <= 64 for item in callbacks)
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert all(isinstance(button, InlineKeyboardButton) for button in buttons)


def test_checkout_uses_an_expandable_fair_use_block() -> None:
    from aiogram.types import InputRichBlockDetails

    message = RichMessages.plan_checkout(
        name="Skye Plus",
        emoji="🌙",
        stars=499,
        model_label="Luna",
        recurring=True,
    )
    assert message.blocks
    details = message.blocks[-1]
    assert isinstance(details, InputRichBlockDetails)
    assert details.summary == "Fair Use and models"
    assert "Fair Use" in str(details.blocks[0].text)
    assert "Luna" in str(details.blocks[1].text)
    assert "Terra" in str(details.blocks[1].text)
    assert "Sol" in str(details.blocks[1].text)
