"""Who is allowed to talk to this bot at all.

The product is the Android app. This bot is an operator's console: it
approves Xray updates, grants test access, and links accounts. None of that
is for the public, and two of them are actively dangerous in public hands —
granting access bypasses the advertising that pays for the servers, and
approving an update restarts nodes.

Implemented as a middleware rather than a check inside each handler,
because a check inside each handler is a check somebody forgets when they
add the next one. Everything arriving at the router passes through here
first, so a new handler is gated by default rather than by remembering.

`TELEGRAM_ADMIN_CHAT_ID` plus `TELEGRAM_ALLOWED_CHAT_IDS` is the whole
allowlist. Empty means nobody, which is the right way round: a bot that
answered everyone because its configuration was blank would be the failure
nobody notices until the bill arrives.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from botapp import texts
from botapp.config import get_settings

logger = logging.getLogger(__name__)


def allowed_chat_ids() -> set[str]:
    settings = get_settings()
    allowed = {
        chunk.strip()
        for chunk in settings.telegram_allowed_chat_ids.split(",")
        if chunk.strip()
    }
    if settings.telegram_admin_chat_id:
        allowed.add(str(settings.telegram_admin_chat_id).strip())
    return allowed


def is_allowed(telegram_id: int | None) -> bool:
    return telegram_id is not None and str(telegram_id) in allowed_chat_ids()


class AdminOnlyMiddleware(BaseMiddleware):
    """Drops anything from a chat that is not on the allowlist."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if is_allowed(getattr(user, "id", None)):
            return await handler(event, data)

        logger.info("ignoring bot traffic from %s", getattr(user, "id", "unknown"))

        # Answered rather than silently dropped: somebody who found the bot
        # deserves to know it is not the product, and a bot that never
        # replies looks broken rather than closed.
        if isinstance(event, Message):
            await event.answer(texts.NOT_FOR_USERS)
        elif isinstance(event, CallbackQuery):
            await event.answer(texts.NOT_FOR_USERS, show_alert=True)
        return None
