"""Bot entrypoint.

The HeadApi instance is injected into every handler through the
dispatcher's workflow data, so handlers never construct their own client
and tests can pass a fake one.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from botapp.access_control import AdminOnlyMiddleware, allowed_chat_ids
from botapp.api_client import HeadApi
from botapp.config import get_settings
from botapp.handlers import router
from botapp.outbox import run_outbox_worker
from botapp.updates import run_updates_worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    if not settings.head_admin_token:
        raise SystemExit("HEAD_ADMIN_TOKEN is not set (must match ADMIN_API_TOKEN on the head)")

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    api = HeadApi()

    dispatcher = Dispatcher()
    # Outer, so it runs before filters and covers every update type. A
    # handler added later is gated by default rather than by remembering.
    dispatcher.message.outer_middleware(AdminOnlyMiddleware())
    dispatcher.callback_query.outer_middleware(AdminOnlyMiddleware())
    dispatcher.include_router(router)
    dispatcher["api"] = api

    if not allowed_chat_ids():
        # Not fatal: the bot still runs and refuses everyone, which is the
        # safe direction. But it will look broken, so say why.
        logger.warning(
            "no TELEGRAM_ADMIN_CHAT_ID or TELEGRAM_ALLOWED_CHAT_IDS set — "
            "the bot will answer nobody"
        )

    workers = [
        asyncio.create_task(run_outbox_worker(bot, api)),
        asyncio.create_task(run_updates_worker(bot, api)),
    ]
    try:
        logger.info("starting polling")
        await dispatcher.start_polling(bot)
    finally:
        for worker in workers:
            worker.cancel()
        await api.aclose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
