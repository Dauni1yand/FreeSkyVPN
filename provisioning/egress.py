#!/usr/bin/env python3
"""A way out to Telegram, through a server of your own.

Telegram is blocked in Russia, and the head is deliberately hosted there.
That breaks two things at once: the bot cannot poll for updates, and the
head cannot deliver the alert that fires when every node goes dark.

Rather than add a second circumvention technique, this reuses the one the
product already ships. It dials a VLESS+Reality server — normally one of
your own nodes — with the same protocol that serves paying users, and
offers the result to the other containers as a plain SOCKS5 proxy. To an
observer the connection is another customer using the VPN.

Configuration is one `vless://` link in EGRESS_VLESS_URL. Get one with:

    docker compose exec head python -m app.cli egress-url

The link may point anywhere that speaks VLESS+Reality, not only at your own
fleet. Pointing it at an independent server costs another machine and buys
something real: alerts that still arrive when your own nodes are the thing
that broke.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from urllib.parse import parse_qs, unquote, urlparse

SOCKS_PORT = int(os.environ.get("EGRESS_SOCKS_PORT", "1080"))
XRAY = os.environ.get("XRAY_BINARY", "/usr/local/bin/xray")


def parse_vless(url: str) -> dict:
    """Turn a vless:// link into the fields Xray's outbound needs.

    Mirrors head/app/services/vless_link.py, which writes these links, and
    android/.../XrayConfigBuilder.kt, which consumes them on the client.
    All three have to agree or the handshake fails.
    """
    if not url.startswith("vless://"):
        raise ValueError("ссылка должна начинаться с vless://")

    parsed = urlparse(url)
    if not parsed.hostname or not parsed.port or not parsed.username:
        raise ValueError("в ссылке нет uuid, хоста или порта")

    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    if q.get("security") != "reality":
        raise ValueError(f"поддерживается только security=reality, а тут {q.get('security')!r}")
    for required in ("pbk", "sid", "sni"):
        if not q.get(required):
            raise ValueError(f"в ссылке нет параметра {required}")

    return {
        "uuid": unquote(parsed.username),
        "host": parsed.hostname,
        "port": parsed.port,
        "network": q.get("type", "tcp"),
        "sni": q["sni"],
        "fingerprint": q.get("fp", "chrome"),
        "public_key": q["pbk"],
        "short_id": q["sid"],
        "flow": q.get("flow") or None,
        "service_name": q.get("serviceName"),
        "path": q.get("path"),
    }


def build_config(link: dict, socks_port: int) -> dict:
    user: dict = {"id": link["uuid"], "encryption": "none"}
    # Omitted rather than sent empty: Xray treats an empty flow as a
    # different setting from an absent one on some transports.
    if link["flow"]:
        user["flow"] = link["flow"]

    stream: dict = {
        "network": link["network"],
        "security": "reality",
        "realitySettings": {
            "serverName": link["sni"],
            "fingerprint": link["fingerprint"],
            "publicKey": link["public_key"],
            "shortId": link["short_id"],
            "spiderX": "/",
        },
    }
    if link["network"] == "grpc":
        stream["grpcSettings"] = {"serviceName": link["service_name"] or ""}
    elif link["network"] == "xhttp":
        stream["xhttpSettings"] = {"path": link["path"] or "/"}

    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                # Reachable only on the compose network — this service
                # publishes no host ports. An open proxy on the public
                # internet is found by scanners within hours.
                "listen": "0.0.0.0",
                "port": socks_port,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True},
            }
        ],
        "outbounds": [
            {
                "tag": "proxy",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {"address": link["host"], "port": link["port"], "users": [user]}
                    ]
                },
                "streamSettings": stream,
            }
        ],
    }


def _misconfigured(*lines: str) -> int:
    """Complain, then stall before exiting.

    The container is `restart: always`, because xray dying for a real
    reason should bring it back. A configuration mistake is not that: it
    will fail identically forever, and exiting immediately turns the log
    into a wall of the same message several times a second, which buries
    the message it is trying to deliver.
    """
    for line in lines:
        # flush по каждой строке: stderr буферизуется поблочно, когда он
        # перенаправлен — а в контейнере он перенаправлен всегда. Без
        # этого сообщение доходит до docker logs через полминуты, а если
        # контейнер успеют остановить, не доходит вовсе.
        print(line, file=sys.stderr, flush=True)
    time.sleep(30)
    return 1


def main() -> int:
    url = os.environ.get("EGRESS_VLESS_URL", "").strip()
    if not url:
        return _misconfigured(
            "EGRESS_VLESS_URL не задан.",
            "Получить ссылку: docker compose exec head python -m app.cli egress-url",
        )

    try:
        link = parse_vless(url)
    except ValueError as exc:
        return _misconfigured(f"не разобрать EGRESS_VLESS_URL: {exc}")

    if not os.access(XRAY, os.X_OK):
        return _misconfigured(
            f"нет исполняемого файла xray по пути {XRAY}",
            "Этот контейнер собирается из образа головы, где xray уже есть.",
            "Если образ свой — укажите путь в XRAY_BINARY.",
        )

    config = build_config(link, SOCKS_PORT)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(config, handle)
        config_path = handle.name

    print(
        f"выход через {link['host']}:{link['port']} ({link['network']}), "
        f"SOCKS5 на порту {SOCKS_PORT}",
        flush=True,
    )
    os.execv(XRAY, [XRAY, "run", "-c", config_path])


if __name__ == "__main__":
    raise SystemExit(main())
