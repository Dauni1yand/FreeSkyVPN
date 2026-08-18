"""Delivers config changes the head decided on but cannot deliver itself.

When an inbound is declared blocked, the head migrates everyone who was on
it and queues a row per user (head/app/db/models/outbox.py). This worker
turns those rows into Telegram messages, so a user whose server died learns
about it from a message rather than by discovering their VPN stopped
working.

Acknowledgement is per row and happens only after Telegram accepted the
message, so a crash mid-drain redelivers rather than silently dropping. A
row that cannot ever be delivered — the user blocked the bot, say — is
acknowledged with the error recorded, because retrying it forever would
wedge the queue behind a message nobody will receive.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from botapp import texts
from botapp.api_client import HeadApi
from botapp.config import get_settings

logger = logging.getLogger(__name__)


async def deliver_pending(bot: Bot, api: HeadApi) -> int:
    """Drain one batch. Returns how many were delivered."""
    pending = await api.pending_pushes()
    delivered = 0

    for push in pending:
        if push.telegram_id is None or push.vless_url is None:
            # Not reachable over Telegram (an Android-only account), or the
            # user has no active assignment any more. Either way this row is
            # not actionable here; drop it rather than blocking the queue.
            await api.ack_push(push.push_id, error="no telegram identity or no active config")
            continue

        try:
            await bot.send_message(
                chat_id=int(push.telegram_id),
                text=texts.new_config_pushed(push.vless_url),
            )
        except TelegramRetryAfter as exc:
            # Flood control: stop the batch and let the next tick resume, so
            # the remaining rows are not burned against the same limit.
            logger.warning("rate limited by Telegram, pausing batch for %ss", exc.retry_after)
            break
        except TelegramForbiddenError as exc:
            logger.info("user %s unreachable (%s), dropping push", push.telegram_id, exc)
            await api.ack_push(push.push_id, error=str(exc))
            continue
        except Exception as exc:  # one bad row must not stop the drain
            logger.exception("failed to deliver push %s", push.push_id)
            await api.ack_push(push.push_id, error=str(exc))
            continue

        await api.ack_push(push.push_id)
        delivered += 1

    return delivered


async def run_outbox_worker(bot: Bot, api: HeadApi) -> None:
    interval = get_settings().outbox_poll_seconds
    while True:
        try:
            count = await deliver_pending(bot, api)
            if count:
                logger.info("delivered %d config push(es)", count)
        except Exception:
            # The head being briefly unreachable must not kill the worker.
            logger.exception("outbox drain failed, retrying next tick")
        await asyncio.sleep(interval)
