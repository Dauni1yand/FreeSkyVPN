"""Out-of-band alerting for when a node is fully isolated (see channel.py) —
the last-resort signal that deliberately does not depend on any node being
reachable, so it still works when every node's control channel is blocked
at once.

Telegram is the natural choice here rather than a dedicated status page or
SMS gateway: this project already runs a bot, so the delivery path exists.
Deliberately not used to carry configs or bulk data — just a heartbeat and
alert line to a human.

One caveat that used to be stated the other way round in this docstring:
api.telegram.org is *not* reachable from Russia, which is where the head is
meant to sit. So the alert goes through `telegram_proxy_url` when one is
configured. That does put an alert about unreachable nodes on a path that
may itself run through a node — see the compose file's `egress` service.
Circular in the worst case, and worth knowing: an egress pointed at an
independent server keeps the alerting honest, one pointed at your own fleet
is convenient but goes quiet exactly when the whole fleet does.
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
        with httpx.Client(proxy=settings.telegram_proxy_url or None, timeout=5.0) as client:
            client.post(url, json={"chat_id": settings.telegram_admin_chat_id, "text": text})
    except httpx.HTTPError:
        logger.exception("Failed to deliver admin alert via Telegram")
