from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, replace

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    RefundedPayment,
    SuccessfulPayment,
)

from .access import AccessService
from .config import HOSTED_MODEL, clamp_model
from .db import Database
from .models import ChatSettings, PlanId, RequestContext, Scope, StarEntitlement
from .rich import RichMessages

log = structlog.get_logger()

STARS_CURRENCY = "XTR"
SUBSCRIPTION_PERIOD = 2_592_000


class BillingError(ValueError):
    """User-facing Stars billing failure."""


@dataclass(frozen=True, slots=True)
class StarPlan:
    id: PlanId
    name: str
    emoji: str
    stars: int
    recurring: bool
    invoice_title: str
    invoice_description: str

    @property
    def button_label(self) -> str:
        if self.recurring:
            return f"{self.name} · {self.stars} ⭐ / month"
        return f"{self.name} · {self.stars} ⭐"

    @property
    def pay_label(self) -> str:
        if self.recurring:
            return f"Subscribe · {self.stars} ⭐"
        return f"Pay {self.stars} ⭐"


PLANS: dict[PlanId, StarPlan] = {
    "plus": StarPlan(
        id="plus",
        name="Skye Plus",
        emoji="🌙",
        stars=449,
        recurring=True,
        invoice_title="Skye Plus",
        invoice_description=(
            "Monthly Skye Plus. Expanded daily message allowance and your own agents. "
            "Renews every 30 days in Telegram Stars."
        ),
    ),
}


def plan_by_id(plan_id: str) -> StarPlan:
    if plan_id not in PLANS:
        raise BillingError("Unknown Skye plan.")
    return PLANS[plan_id]


def encode_payload(plan_id: PlanId, user_id: int, secret: str) -> str:
    body = f"{plan_id}:{user_id}"
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{body}:{signature}"


def decode_payload(payload: str, secret: str) -> tuple[StarPlan, int]:
    parts = payload.split(":")
    if len(parts) != 3:
        raise BillingError("This invoice is not valid.")
    plan_id, raw_user, signature = parts
    body = f"{plan_id}:{raw_user}"
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(expected, signature):
        raise BillingError("This invoice is not valid.")
    try:
        user_id = int(raw_user)
    except ValueError as error:
        raise BillingError("This invoice is not valid.") from error
    return stored_plan(plan_id), user_id


def stored_plan(plan_id: str) -> StarPlan:
    parsed = _as_plan_id(plan_id)
    if parsed is None:
        raise BillingError("Unknown Skye plan.")
    if parsed in PLANS:
        return PLANS[parsed]
    return StarPlan(
        id=parsed,
        name="Skye",
        emoji="🌙",
        stars=0,
        recurring=parsed != "trial",
        invoice_title="Skye",
        invoice_description="Skye access.",
    )


def _as_plan_id(plan_id: str) -> PlanId | None:
    if plan_id == "trial":
        return "trial"
    if plan_id == "plus":
        return "plus"
    if plan_id == "super":
        return "super"
    if plan_id == "ultra":
        return "ultra"
    return None


def remaining_copy(entitlement: StarEntitlement, now: int) -> str:
    days = entitlement.days_left(now)
    if days <= 0:
        remaining = "Ends today."
    elif days == 1:
        remaining = "1 day left."
    else:
        remaining = f"{days} days left."
    if entitlement.plan == "trial":
        return f"{remaining} Your Plus preview ends automatically."
    if entitlement.auto_renew:
        return (
            f"{remaining} Renews automatically. Telegram Stars will be charged again "
            "at the end of this period."
        )
    return (
        f"{remaining} This period will end, then Skye Plus stops. "
        "There will be no further charges."
    )


class BillingService:
    def __init__(self, database: Database, secret: str) -> None:
        self.database = database
        self.secret = secret

    def payload(self, plan: StarPlan, user_id: int) -> str:
        return encode_payload(plan.id, user_id, self.secret)

    async def entitlement(self, user_id: int, *, now: int | None = None) -> StarEntitlement | None:
        return await self.database.active_entitlement(user_id, now=now)

    async def complimentary(self, context: RequestContext, access: AccessService) -> bool:
        if access.is_owner(context.user_id):
            return True
        return await self.database.access_effect(context.scope) == "allow"

    async def clamp_settings(
        self, context: RequestContext, settings: ChatSettings, access: AccessService
    ) -> ChatSettings:
        del context, access
        if settings.model == HOSTED_MODEL:
            return settings
        return replace(settings, model=clamp_model(settings.model))

    def validate_checkout(
        self,
        *,
        user_id: int,
        currency: str,
        total_amount: int,
        invoice_payload: str,
        entitlement: StarEntitlement | None,
        now: int | None = None,
        renewal: bool = False,
    ) -> StarPlan:
        if currency != STARS_CURRENCY:
            raise BillingError("Skye plans are paid in Telegram Stars.")
        plan, payload_user = decode_payload(invoice_payload, self.secret)
        if payload_user != user_id:
            raise BillingError("This invoice is for a different Telegram account.")
        if renewal:
            return plan
        if plan.id not in PLANS:
            raise BillingError("Unknown Skye plan.")
        if total_amount != plan.stars:
            raise BillingError("This invoice no longer matches the Skye plan.")
        current = entitlement
        if current is not None and now is not None and not current.active(now):
            current = None
        if (
            current is not None
            and current.plan == plan.id
            and current.auto_renew
            and (now is None or current.active(now))
        ):
            raise BillingError("This plan is already active.")
        return plan

    async def apply_payment(
        self, user_id: int, payment: SuccessfulPayment, bot: Bot | None = None
    ) -> StarEntitlement:
        now = int(time.time())
        existing = await self.database.star_payment(payment.telegram_payment_charge_id)
        if existing is not None:
            current = await self.database.star_entitlement(user_id)
            if current is None or existing[0] != user_id:
                raise BillingError("This payment was already recorded.")
            return current
        plan = self.validate_checkout(
            user_id=user_id,
            currency=payment.currency,
            total_amount=payment.total_amount,
            invoice_payload=payment.invoice_payload,
            entitlement=await self.database.star_entitlement(user_id),
            now=now,
            renewal=bool(payment.is_recurring and not payment.is_first_recurring),
        )
        recorded = await self.database.record_star_payment(
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            user_id=user_id,
            plan=plan.id,
            stars=payment.total_amount,
            invoice_payload=payment.invoice_payload,
            is_recurring=bool(payment.is_recurring),
            is_first_recurring=bool(payment.is_first_recurring),
            subscription_expiration_date=payment.subscription_expiration_date,
        )
        current = await self.database.star_entitlement(user_id)
        if not recorded:
            if current is None:
                raise BillingError("This payment was already recorded.")
            return current
        if payment.is_recurring and not payment.is_first_recurring and current is not None:
            expires_at = payment.subscription_expiration_date or (now + SUBSCRIPTION_PERIOD)
            entitlement = await self.database.extend_star_entitlement(user_id, expires_at)
            await self.database.record_product_event(user_id, "subscription_renewed")
            return entitlement
        expires_at = payment.subscription_expiration_date or (now + SUBSCRIPTION_PERIOD)
        charge_id = payment.telegram_payment_charge_id if plan.recurring else None
        if (
            current is not None
            and current.auto_renew
            and current.telegram_payment_charge_id
            and current.plan != plan.id
            and bot is not None
        ):
            await self._stop_star_renewal(
                bot, user_id, current.telegram_payment_charge_id
            )
        entitlement = await self.database.upsert_star_entitlement(
            user_id=user_id,
            plan=plan.id,
            auto_renew=plan.recurring,
            expires_at=expires_at,
            telegram_payment_charge_id=charge_id,
            trial_used=False,
        )
        await self._align_model(user_id)
        await self.database.record_product_event(user_id, "subscription_started")
        return entitlement

    async def cancel_renewal(self, user_id: int, bot: Bot) -> StarEntitlement:
        current = await self.entitlement(user_id)
        if current is None or not current.auto_renew:
            raise BillingError("Nothing is set to renew.")
        if current.telegram_payment_charge_id:
            await self._stop_star_renewal(bot, user_id, current.telegram_payment_charge_id)
        return await self.database.set_star_auto_renew(user_id, False)

    async def _stop_star_renewal(self, bot: Bot, user_id: int, charge_id: str) -> None:
        try:
            await bot.edit_user_star_subscription(
                user_id=user_id,
                telegram_payment_charge_id=charge_id,
                is_canceled=True,
            )
        except TelegramBadRequest as error:
            log.warning("star_cancel_failed", user_id=user_id, error=type(error).__name__)

    async def apply_refund(self, user_id: int, payment: RefundedPayment) -> StarEntitlement | None:
        recorded = await self.database.star_payment(payment.telegram_payment_charge_id)
        current = await self.database.star_entitlement(user_id)
        if recorded is None or current is None or recorded[0] != user_id:
            return current
        if recorded[1] != current.plan:
            return current
        return await self.database.expire_star_entitlement(user_id, int(time.time()))

    async def _align_model(self, user_id: int) -> None:
        scope = Scope("user", user_id)
        settings = await self.database.get_settings(scope)
        if settings.model != HOSTED_MODEL:
            await self.database.set_model(scope, HOSTED_MODEL)


class AccountPanel:
    def __init__(
        self,
        billing: BillingService,
        access: AccessService,
        rich: RichMessages,
        bot: Bot,
    ) -> None:
        self.billing = billing
        self.access = access
        self.rich = rich
        self.bot = bot

    async def show(
        self,
        message: Message,
        context: RequestContext,
        *,
        edit: bool = False,
        notice: str | None = None,
    ) -> None:
        if context.chat_type != "private":
            content = self.rich.prompt(
                "Account",
                "Account and Stars billing are available in a private chat.",
            )
            if edit:
                await self.rich.edit(message, content)
            else:
                await self.rich.send(message, content)
            return
        if await self._banned(context):
            content = self.rich.prompt("Account", "This account is banned.")
            if edit:
                await self.rich.edit(message, content)
            else:
                await self.rich.send(message, content)
            return
        now = int(time.time())
        entitlement = await self.billing.entitlement(context.user_id, now=now)
        owner = self.access.is_owner(context.user_id)
        complimentary = await self.billing.complimentary(context, self.access)
        plan = PLANS.get(entitlement.plan) if entitlement is not None else None
        if entitlement is not None and entitlement.plan == "trial":
            plan = StarPlan(
                "trial",
                "Skye Plus preview",
                "🌙",
                0,
                False,
                "Skye Plus preview",
                "Seven-day Skye Plus preview.",
            )
        content = self.rich.account(
            owner=owner,
            complimentary=complimentary,
            plan_name=None if plan is None else plan.name,
            status=None if entitlement is None else remaining_copy(entitlement, now),
            notice=notice,
        )
        markup = self._home_keyboard(entitlement, owner=owner)
        if edit:
            await self.rich.edit(message, content, reply_markup=markup)
        else:
            await self.rich.send(message, content, reply_markup=markup)

    async def show_checkout(
        self, message: Message, context: RequestContext, plan_id: str
    ) -> None:
        plan = plan_by_id(plan_id)
        now = int(time.time())
        entitlement = await self.billing.entitlement(context.user_id, now=now)
        self.billing.validate_checkout(
            user_id=context.user_id,
            currency=STARS_CURRENCY,
            total_amount=plan.stars,
            invoice_payload=self.billing.payload(plan, context.user_id),
            entitlement=entitlement,
            now=now,
        )
        link = await self._invoice_link(plan, context.user_id)
        await self.rich.edit(
            message,
            self.rich.plan_checkout(
                name=plan.name,
                emoji=plan.emoji,
                stars=plan.stars,
                recurring=plan.recurring,
            ),
            reply_markup=self._checkout_keyboard(plan, link),
        )

    async def handle_callback(
        self, message: Message, context: RequestContext, action: list[str]
    ) -> None:
        if context.chat_type != "private":
            raise BillingError("Account and Stars billing are available in a private chat.")
        if await self._banned(context):
            raise BillingError("This account is banned.")
        if action == ["home"] or action == ["plans"]:
            await self.show(message, context, edit=True)
        elif len(action) == 2 and action[0] == "plan":
            await self.show_checkout(message, context, action[1])
        elif action == ["cancel"]:
            await self.rich.edit(
                message,
                self.rich.prompt(
                    "Cancel renewal?",
                    "Stop automatic renewal? You keep access until this period ends, "
                    "and there will be no further charges.",
                ),
                reply_markup=self._cancel_keyboard(),
            )
        elif action == ["cancel", "yes"]:
            await self.billing.cancel_renewal(context.user_id, self.bot)
            await self.show(
                message,
                context,
                edit=True,
                notice="Automatic renewal is off. This period will end, then access stops.",
            )
        else:
            raise BillingError("Unknown account action.")

    async def pre_checkout(self, query: PreCheckoutQuery) -> None:
        now = int(time.time())
        try:
            self.billing.validate_checkout(
                user_id=query.from_user.id,
                currency=query.currency,
                total_amount=query.total_amount,
                invoice_payload=query.invoice_payload,
                entitlement=await self.billing.database.star_entitlement(query.from_user.id),
                now=now,
            )
        except BillingError as error:
            await query.answer(ok=False, error_message=str(error)[:200])
            return
        await query.answer(ok=True)

    async def successful_payment(self, message: Message, context: RequestContext) -> None:
        payment = message.successful_payment
        if payment is None:
            return
        try:
            entitlement = await self.billing.apply_payment(context.user_id, payment, self.bot)
        except BillingError as error:
            log.warning("star_payment_rejected", user_id=context.user_id, error=str(error)[:200])
            await self.rich.send(message, str(error))
            return
        plan = PLANS.get(entitlement.plan)
        heading = plan.name if plan is not None else "Account"
        await self.rich.send(
            message,
            self.rich.prompt(
                heading,
                [
                    f"{plan.emoji} {plan.name} is active. " if plan is not None else "",
                    remaining_copy(entitlement, int(time.time())),
                ],
            ),
        )

    async def refunded_payment(self, message: Message, context: RequestContext) -> None:
        payment = message.refunded_payment
        if payment is None:
            return
        await self.billing.apply_refund(context.user_id, payment)
        await self.rich.send(
            message,
            "That Stars payment was refunded. If nothing else is active, access has ended.",
        )

    async def paysupport(self, message: Message) -> None:
        await self.rich.send(
            message,
            self.rich.prompt(
                "Payment support",
                "Skye handles Telegram Stars billing here. Telegram support cannot help "
                "with these purchases. Describe the issue in this private chat.",
            ),
        )

    async def terms(self, message: Message) -> None:
        await self.rich.send(message, self.rich.plan_terms())

    async def _banned(self, context: RequestContext) -> bool:
        if self.access.is_owner(context.user_id):
            return False
        return await self.billing.database.access_effect(Scope("user", context.user_id)) == "ban"

    async def _invoice_link(self, plan: StarPlan, user_id: int) -> str:
        prices = [LabeledPrice(label=plan.name, amount=plan.stars)]
        payload = self.billing.payload(plan, user_id)
        try:
            if plan.recurring:
                return await self.bot.create_invoice_link(
                    title=plan.invoice_title,
                    description=plan.invoice_description,
                    payload=payload,
                    currency=STARS_CURRENCY,
                    prices=prices,
                    subscription_period=SUBSCRIPTION_PERIOD,
                )
            return await self.bot.create_invoice_link(
                title=plan.invoice_title,
                description=plan.invoice_description,
                payload=payload,
                currency=STARS_CURRENCY,
                prices=prices,
            )
        except TelegramBadRequest as error:
            raise BillingError("Stars payments are not available on this bot yet.") from error

    @staticmethod
    def _home_keyboard(
        entitlement: StarEntitlement | None,
        *,
        owner: bool,
    ) -> InlineKeyboardMarkup | None:
        if owner:
            return None
        rows: list[list[InlineKeyboardButton]] = []
        plus = PLANS["plus"]
        if entitlement is None or entitlement.plan != plus.id:
            rows.append(
                [InlineKeyboardButton(text=plus.button_label, callback_data="acct:plan:plus")]
            )
        if entitlement is not None and entitlement.auto_renew:
            rows.append(
                [InlineKeyboardButton(text="Cancel renewal", callback_data="acct:cancel")]
            )
        return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

    @staticmethod
    def _checkout_keyboard(plan: StarPlan, link: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=plan.pay_label, url=link)],
                [InlineKeyboardButton(text="‹ Back", callback_data="acct:home")],
            ]
        )

    @staticmethod
    def _cancel_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Cancel renewal", callback_data="acct:cancel:yes"
                    ),
                    InlineKeyboardButton(text="Keep plan", callback_data="acct:home"),
                ]
            ]
        )
