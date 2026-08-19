"""The head's alert path has to survive Telegram being blocked.

api.telegram.org is unreachable from Russia, which is where the head is
meant to sit — so the one message that fires when every node goes dark
would never be delivered.

These tests drive a real SOCKS5 proxy and assert on what it was asked to
connect to. The failure being guarded against is a connection going
somewhere other than through the proxy, and only the proxy's own view can
show that. What happens after the CONNECT — TLS to a host that does not
exist here — is not what is under test.
"""

from __future__ import annotations

import socket

from app.config import get_settings
from app.node_manager import telegram_signal
from tests.socks_server import socks5_server


def _configure(monkeypatch, **overrides) -> None:
    get_settings.cache_clear()
    for key, value in overrides.items():
        monkeypatch.setenv(key.upper(), value)
    get_settings.cache_clear()


def test_the_alert_goes_through_the_proxy_when_one_is_set(monkeypatch):
    seen: list[tuple[str, int]] = []
    with socks5_server(record=seen) as proxy_port:
        _configure(
            monkeypatch,
            telegram_bot_token="123:ABC",
            telegram_admin_chat_id="42",
            telegram_proxy_url=f"socks5://127.0.0.1:{proxy_port}",
        )
        telegram_signal.notify_admin("нода недоступна")

    assert seen == [("api.telegram.org", 443)], (
        "прокси не получил запрос — значит, соединение пошло напрямую"
    )


def test_without_a_proxy_it_does_not_quietly_use_one(monkeypatch):
    """A direct call must stay direct.

    Not a formality: if an empty setting were passed through as an address,
    every deployment outside Russia would break at once.
    """
    seen: list[tuple[str, int]] = []
    with socks5_server(record=seen) as proxy_port:
        _configure(
            monkeypatch,
            telegram_bot_token="123:ABC",
            telegram_admin_chat_id="42",
            telegram_proxy_url="",
        )
        telegram_signal.notify_admin("прямой путь")
        assert proxy_port  # запущен намеренно: он должен остаться нетронутым

    assert seen == [], "запрос ушёл через прокси, хотя тот не настроен"


def test_a_dead_proxy_does_not_take_the_caller_down(monkeypatch):
    """Alerting is best-effort and must never raise into its caller.

    This runs inside the isolation sweep. An exception here would turn
    "one node is unreachable" into a failed sweep for every node.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]

    _configure(
        monkeypatch,
        telegram_bot_token="123:ABC",
        telegram_admin_chat_id="42",
        telegram_proxy_url=f"socks5://127.0.0.1:{dead_port}",
    )
    telegram_signal.notify_admin("никто не услышит")


def test_nothing_is_sent_when_telegram_is_not_configured(monkeypatch):
    _configure(monkeypatch, telegram_bot_token="", telegram_admin_chat_id="")
    telegram_signal.notify_admin("некуда слать")
