"""Out-of-band alerting for when a node is fully isolated (see channel.py) —
the last-resort signal that deliberately does not depend on any node being
reachable, so it still works when every node's control channel is blocked
at once.

Telegram is the natural choice here rather than a dedicated status page or
SMS gateway: this project already runs a bot (the MVP client itself), and
Telegram's own infrastructure is broadly reachable even in environments
that actively target VPN-adjacent traffic. Deliberately not used to carry
configs or bulk data — just a heartbeat/alert line to a human.
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


def notify_admin(text: str) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_admin_chat_id:
        logger.warning("Telegram alerting is not configured, dropping message: %s", text)
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        httpx.post(url, json={"chat_id": settings.telegram_admin_chat_id, "text": text}, timeout=5.0)
    except httpx.HTTPError:
        logger.exception("Failed to deliver admin alert via Telegram")
