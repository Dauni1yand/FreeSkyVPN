"""Bot handlers.

Every handler is a thin translation between a Telegram interaction and one
head API call. Any decision worth making — which server, whether a config
is dead, what a payment entitles someone to — is made by the head, so that
the Android client in phase 5 inherits identical behaviour for free.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from botapp import keyboards, texts
from botapp.api_client import HeadApi, HeadApiError
from botapp.config import get_settings

logger = logging.getLogger(__name__)
router = Router()


async def _user_id(api: HeadApi, telegram_id: int) -> str:
    return await api.login_telegram(telegram_id)


@router.message(CommandStart())
async def on_start(message: Message, api: HeadApi) -> None:
    # Registration is implicit: /start is the account. Everything else the
    # user does resolves to the same head-side user_id, so linking a phone or
    # the Android app later attaches to this same account (blueprint §05).
    await _user_id(api, message.from_user.id)
    await message.answer(texts.WELCOME, reply_markup=keyboards.main_menu())


@router.callback_query(F.data == keyboards.CB_MENU)
async def on_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text(texts.WELCOME, reply_markup=keyboards.main_menu())
    await callback.answer()


@router.callback_query(F.data == keyboards.CB_CONNECT)
async def on_connect(callback: CallbackQuery, api: HeadApi) -> None:
    await callback.answer(texts.CONNECTING)
    user_id = await _user_id(api, callback.from_user.id)

    try:
        config = await api.connect(user_id)
    except HeadApiError as exc:
        logger.warning("connect failed for %s: %s", callback.from_user.id, exc)
        text = texts.NO_CAPACITY if exc.status_code == 503 else texts.GENERIC_ERROR
        await callback.message.answer(text, reply_markup=keyboards.main_menu())
        return

    await callback.message.answer(
        texts.config_message(config.vless_url), reply_markup=keyboards.main_menu()
    )


@router.callback_query(F.data == keyboards.CB_REPORT)
async def on_report_failure(callback: CallbackQuery, api: HeadApi) -> None:
    await callback.answer()
    user_id = await _user_id(api, callback.from_user.id)

    try:
        result = await api.report_failure(user_id)
    except HeadApiError as exc:
        if exc.status_code == 429:
            # detail reads "try again in Ns"; show the number rather than the
            # raw sentence so the copy stays in one language
            seconds = "".join(ch for ch in exc.detail if ch.isdigit()) or "30"
            await callback.message.answer(texts.REPORT_TOO_SOON.format(seconds=seconds))
            return
        if exc.status_code == 409:
            await callback.message.answer(
                texts.NO_ACTIVE_CONFIG, reply_markup=keyboards.main_menu()
            )
            return
        logger.warning("report-failure failed for %s: %s", callback.from_user.id, exc)
        text = texts.NO_CAPACITY if exc.status_code == 503 else texts.GENERIC_ERROR
        await callback.message.answer(text, reply_markup=keyboards.main_menu())
        return

    await callback.message.answer(
        texts.failure_handled(result.config.vless_url, result.inbound_declared_dead),
        reply_markup=keyboards.main_menu(),
    )


@router.callback_query(F.data == keyboards.CB_SUBSCRIPTION)
async def on_subscription(callback: CallbackQuery, api: HeadApi) -> None:
    await callback.answer()
    user_id = await _user_id(api, callback.from_user.id)

    subscription = await api.subscription(user_id)
    plans = await api.plans() if get_settings().payment_provider_token else []

    await callback.message.edit_text(
        texts.subscription_status(subscription.active, subscription.type, subscription.expires_at),
        reply_markup=keyboards.subscription_menu(
            plans, trial_available=subscription.trial_available
        ),
    )


@router.callback_query(F.data == keyboards.CB_TRIAL)
async def on_trial(callback: CallbackQuery, api: HeadApi) -> None:
    await callback.answer()
    user_id = await _user_id(api, callback.from_user.id)

    try:
        subscription = await api.start_trial(user_id)
    except HeadApiError as exc:
        if exc.status_code == 409:
            await callback.message.answer(texts.TRIAL_ALREADY_USED)
            return
        raise

    await callback.message.answer(
        texts.trial_started(subscription.expires_at), reply_markup=keyboards.main_menu()
    )


@router.callback_query(F.data.startswith(keyboards.CB_BUY_PREFIX))
async def on_buy(callback: CallbackQuery, api: HeadApi) -> None:
    await callback.answer()
    settings = get_settings()
    if not settings.payment_provider_token:
        await callback.message.answer(texts.PAYMENTS_DISABLED)
        return

    plan_code = callback.data.removeprefix(keyboards.CB_BUY_PREFIX)
    plan = next((p for p in await api.plans() if p.code == plan_code), None)
    if plan is None:
        await callback.message.answer(texts.GENERIC_ERROR)
        return

    await callback.message.answer_invoice(
        title=plan.name,
        description=texts.invoice_description(plan.name, plan.duration_days),
        # Telegram echoes the payload back on success — it is how the
        # successful_payment handler knows which plan was bought.
        payload=f"plan:{plan.code}",
        provider_token=settings.payment_provider_token,
        currency=plan.currency,
        # Telegram prices are in minor units. `round`, not `int`: for prices
        # whose float lands just under the cent (1.13 * 100 == 112.999…),
        # truncating would undercharge by a kopeck.
        prices=[LabeledPrice(label=plan.name, amount=round(plan.price * 100))],
    )


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    # Telegram requires an answer within 10 seconds or the payment fails.
    # Nothing here can legitimately reject a purchase, so always approve.
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, api: HeadApi) -> None:
    payment = message.successful_payment
    plan_code = payment.invoice_payload.removeprefix("plan:")
    user_id = await _user_id(api, message.from_user.id)

    subscription = await api.confirm_payment(
        user_id=user_id,
        plan_code=plan_code,
        # Telegram's own charge id — the head uses it to make a repeated
        # notification a no-op rather than a second month.
        provider_payment_id=payment.telegram_payment_charge_id,
        amount=payment.total_amount / 100,
        currency=payment.currency,
    )

    await message.answer(
        texts.payment_succeeded(subscription.expires_at), reply_markup=keyboards.main_menu()
    )


# --- Xray updates (admin only) ------------------------------------------


def _is_admin(telegram_id: int) -> bool:
    """Only the configured admin chat may authorise an update.

    Checked here rather than trusted from the keyboard: an inline button's
    callback_data is visible to anyone who can see the message, and a
    forwarded message carries its buttons with it — so the button is a
    convenience, and this is the actual gate.
    """
    configured = get_settings().telegram_admin_chat_id
    return bool(configured) and str(telegram_id) == str(configured)


async def _decide_update(callback: CallbackQuery, api: HeadApi, *, approve: bool) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer(texts.NOT_ADMIN, show_alert=True)
        return

    prefix = keyboards.CB_UPD_APPROVE_PREFIX if approve else keyboards.CB_UPD_DECLINE_PREFIX
    target_version = callback.data[len(prefix) :]

    try:
        changed = await api.decide_update_version(
            target_version, approve=approve, by=f"telegram:{callback.from_user.id}"
        )
    except HeadApiError as exc:
        logger.warning("could not record update decision: %s", exc)
        await callback.answer(texts.GENERIC_ERROR, show_alert=True)
        return

    if not changed:
        # Already answered — most often from the admin panel, or a second tap
        # on the same button. Say so instead of implying something happened.
        await callback.answer(texts.UPDATE_ALREADY_DECIDED, show_alert=True)
        # Drop the buttons so the stale message stops inviting more taps.
        await callback.message.edit_reply_markup(reply_markup=None)
        return

    text = (
        texts.update_queued(target_version, changed)
        if approve
        else texts.update_declined(target_version)
    )
    await callback.message.edit_text(text)
    await callback.answer()


@router.callback_query(F.data.startswith(keyboards.CB_UPD_APPROVE_PREFIX))
async def on_update_approve(callback: CallbackQuery, api: HeadApi) -> None:
    await _decide_update(callback, api, approve=True)


@router.callback_query(F.data.startswith(keyboards.CB_UPD_DECLINE_PREFIX))
async def on_update_decline(callback: CallbackQuery, api: HeadApi) -> None:
    await _decide_update(callback, api, approve=False)
