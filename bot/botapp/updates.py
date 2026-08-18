"""Announces Xray updates to the administrator and reports how they went.

The head finds updates and applies approved ones; it has no way to hold a
conversation, so this worker is the half that asks. It mirrors outbox.py
deliberately — same polling shape, same "acknowledge only after Telegram
accepted it" rule — because the failure it guards against is the same one:
a message that was decided but never delivered.

Two separate deliveries, and both matter. The first asks permission. The
second closes the loop, because an operator who tapped "обновить" and heard
nothing back has no way to tell a slow update from a broken one.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

from botapp import keyboards, texts
from botapp.api_client import HeadApi
from botapp.config import get_settings

logger = logging.getLogger(__name__)


async def announce_pending(bot: Bot, api: HeadApi, admin_chat_id: str) -> int:
    """Ask about every update version nobody has been asked about yet."""
    groups = await api.pending_update_groups()
    sent = 0

    for group in groups:
        try:
            await bot.send_message(
                chat_id=int(admin_chat_id),
                text=texts.update_available(group.target_version, group.nodes),
                reply_markup=keyboards.update_decision(group.target_version, len(group.nodes)),
            )
        except TelegramRetryAfter as exc:
            logger.warning("rate limited announcing updates, pausing %ss", exc.retry_after)
            break
        except Exception:
            # Not acknowledged, so the next tick asks again. An update nobody
            # was asked about must never be silently forgotten.
            logger.exception("could not announce Xray %s", group.target_version)
            continue

        await api.ack_update_notifications(group.update_ids)
        sent += 1

    return sent


async def report_results(bot: Bot, api: HeadApi, admin_chat_id: str) -> int:
    """Tell the operator how the updates they approved turned out."""
    results = await api.update_results()
    reported = 0

    for result in results:
        try:
            await bot.send_message(chat_id=int(admin_chat_id), text=texts.update_result(result))
        except TelegramRetryAfter as exc:
            logger.warning("rate limited reporting updates, pausing %ss", exc.retry_after)
            break
        except Exception:
            logger.exception("could not report update %s", result.update_id)
            # Acknowledged anyway: unlike the question above, a result that
            # cannot be delivered would otherwise be retried forever, and it
            # is visible in the admin panel regardless.
            await api.ack_update_results([result.update_id])
            continue

        await api.ack_update_results([result.update_id])
        reported += 1

    return reported


async def run_updates_worker(bot: Bot, api: HeadApi) -> None:
    settings = get_settings()
    if not settings.telegram_admin_chat_id:
        # Not an error: the admin panel covers the same ground, and a bot
        # with nowhere to send alerts should say so once and stop, not log
        # the same complaint every minute.
        logger.warning("TELEGRAM_ADMIN_CHAT_ID is not set — Xray updates will only appear in the admin panel")
        return

    chat_id = settings.telegram_admin_chat_id
    while True:
        try:
            await announce_pending(bot, api, chat_id)
            await report_results(bot, api, chat_id)
        except Exception:
            # The head being briefly unreachable must not kill the worker.
            logger.exception("Xray update poll failed, retrying next tick")
        await asyncio.sleep(settings.updates_poll_seconds)
