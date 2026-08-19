"""How the bot reaches Telegram.

Telegram is blocked in Russia and the bot runs on the same server as the
head, which is deliberately hosted there. Without a way out, long polling
fails on its first call and the bot never starts — with nothing in the log
that points at the cause.

The proxy has to be opt-in: an installation outside Russia needs a direct
connection, and forcing everyone through a proxy to serve one jurisdiction
would break far more than it fixes.
"""

from __future__ import annotations

from aiogram.client.session.aiohttp import AiohttpSession

from botapp.main import _session


def test_no_proxy_configured_means_a_direct_connection():
    session = _session("")
    assert isinstance(session, AiohttpSession)
    assert session.proxy is None


def test_a_configured_proxy_is_applied():
    session = _session("socks5://egress:1080")
    assert session.proxy == "socks5://egress:1080"


def test_an_http_proxy_works_too():
    """Not every operator will run the egress container.

    A rented HTTP proxy or an SSH tunnel is a legitimate answer, and
    aiogram refuses any proxied session without aiohttp-socks installed —
    including plain http:// — so this also pins that dependency.
    """
    session = _session("http://127.0.0.1:8118")
    assert session.proxy == "http://127.0.0.1:8118"


def test_the_connector_is_the_proxy_aware_one():
    """The setting has to change the connector, not just be stored.

    aiogram keeps `proxy` as an attribute either way; what decides whether
    traffic is actually tunnelled is which connector it builds.
    """
    direct = _session("")
    proxied = _session("socks5://egress:1080")
    assert proxied._connector_type is not direct._connector_type
    assert "Proxy" in proxied._connector_type.__name__
