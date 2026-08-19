"""Bot handlers — an operator's console, not a client.

The product is the Android app. This bot approves Xray updates, grants
test access and links accounts, and none of that is for the public: two of
them are actively dangerous in public hands, since granting access bypasses
the advertising that pays for the servers and approving an update restarts
nodes.

Access to all of it is decided once, in access_control.py, by a middleware
that runs before any handler here. Nothing in this file re-checks it.

Every handler is a thin translation between a Telegram interaction and one
head API call. Any decision worth making — which server, whether a config
is dead, whether someone may connect — is made by the head.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

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


@router.message(Command("link"))
async def on_link(message: Message, command: CommandObject, api: HeadApi) -> None:
    """Attach the Android app's anonymous account to this Telegram account.

    The app registers anonymously so nobody has to fill in a form before
    their first connection. That trade costs account recovery, and this is
    where it is bought back.
    """
    code = (command.args or "").strip()
    if not code:
        await message.answer(texts.LINK_USAGE)
        return

    try:
        await api.redeem_link_code(code, message.from_user.id)
    except HeadApiError as exc:
        # 400 carries a reason meant for the user; anything else does not.
        reason = exc.detail if exc.status_code == 400 else "попробуйте ещё раз через минуту"
        await message.answer(texts.link_failed(reason))
        return

    await message.answer(texts.LINK_OK, reply_markup=keyboards.main_menu())


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
        # 402 means the account has no hour bought. From the bot there is no
        # way to buy one — rewarded video needs the app — so this is a
        # signpost rather than an error.
        text = {
            402: texts.NEEDS_AN_AD,
            503: texts.NO_CAPACITY,
        }.get(exc.status_code, texts.GENERIC_ERROR)
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


# --- access, for testing and support only -------------------------------


@router.callback_query(F.data == keyboards.CB_ACCESS)
async def on_access(callback: CallbackQuery, api: HeadApi) -> None:
    """Put this operator's account online without an ad, for testing.

    Reaching this handler already means the allowlist let the message
    through (access_control.AdminOnlyMiddleware), so there is no second
    check here — two places deciding the same thing is how they end up
    disagreeing.
    """
    user_id = await _user_id(api, callback.from_user.id)
    try:
        account = await api.grant_test_access(user_id)
    except HeadApiError as exc:
        logger.warning("test access grant failed for %s: %s", callback.from_user.id, exc)
        await callback.message.answer(texts.GENERIC_ERROR, reply_markup=keyboards.main_menu())
        return

    await callback.answer()
    await callback.message.answer(
        texts.test_access_granted(account.access_seconds_remaining),
        reply_markup=keyboards.main_menu(),
    )
