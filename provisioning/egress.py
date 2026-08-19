#!/usr/bin/env python3
"""A way out to Telegram, through whichever node currently works.

Telegram is blocked in Russia, and the head is deliberately hosted there.
That breaks two things at once: the bot cannot poll for updates, and the
head cannot deliver the alert that fires when every node goes dark.

Rather than add a second circumvention technique, this reuses the one the
product already ships. It dials a node with the same VLESS+Reality that
serves paying users and offers the result to the other containers as a
plain SOCKS5 proxy. To an observer the connection is another customer.

The part that matters is what happens when that node gets blocked, which
is the normal case this exists for rather than an edge one. A proxy pinned
to one node goes down with it and takes the bot and every alert along. So
this asks the head for a config instead of holding a fixed one, checks
that traffic is really flowing, and when it is not, reports the node the
way a phone would and asks for another. The head already knows how to move
a user off a dead inbound; the egress is a user, so it gets that for free
rather than through a second migration path that only runs during outages.

Configuration, all optional:

    HEAD_API_URL           where the head is (default http://head:8000)
    ADMIN_API_TOKEN        must match the head's
    EGRESS_VLESS_URL       pin to a fixed server; disables switching
    EGRESS_SOCKS_PORT      where to listen (default 1080)
    EGRESS_PROBE_URL       what to test with (default api.telegram.org)
    EGRESS_PROBE_SECONDS   how often (default 60)
    EGRESS_PROBE_FAILURES  how many in a row before switching (default 3)

Pinning is worth knowing about: an alert saying every node is unreachable,
sent through one of those nodes, goes quiet exactly when it is needed.
EGRESS_VLESS_URL pointed at an independent server costs another machine
and keeps that path honest.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, unquote, urlparse

import httpx

SOCKS_PORT = int(os.environ.get("EGRESS_SOCKS_PORT", "1080"))
XRAY = os.environ.get("XRAY_BINARY", "/usr/local/bin/xray")
HEAD_URL = os.environ.get("HEAD_API_URL", "http://head:8000").rstrip("/")
ADMIN_TOKEN = os.environ.get("ADMIN_API_TOKEN", "")
PROBE_URL = os.environ.get("EGRESS_PROBE_URL", "https://api.telegram.org")
PROBE_SECONDS = int(os.environ.get("EGRESS_PROBE_SECONDS", "60"))
PROBE_FAILURES = int(os.environ.get("EGRESS_PROBE_FAILURES", "3"))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s egress: %(message)s"
)
log = logging.getLogger("egress")


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


# --- talking to the head ----------------------------------------------------


def _head(path: str) -> dict | None:
    """One call to the head, or None if it could not be made."""
    if not ADMIN_TOKEN:
        log.error("ADMIN_API_TOKEN не задан — голову не спросить")
        return None
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{HEAD_URL}{path}", headers={"X-Admin-Token": ADMIN_TOKEN}
            )
    except httpx.HTTPError as exc:
        log.warning("голова недоступна (%s): %s", path, exc)
        return None

    if response.status_code == 401:
        log.error("голова не приняла ADMIN_API_TOKEN — он разошёлся с её собственным")
        return None
    if response.status_code == 503:
        log.warning("у головы нет свободной ноды: %s", response.text[:160])
        return None
    if response.status_code != 200:
        log.warning("голова ответила %s на %s: %s", response.status_code, path, response.text[:160])
        return None
    return response.json()


def fetch_config() -> str | None:
    body = _head("/api/v1/egress/connect")
    if body is None:
        return None
    log.info("голова выдала конфиг на ноде %s", body.get("node_country", "?"))
    return body["vless_url"]


def report_failure() -> str | None:
    """Tell the head this node is not carrying traffic, and take the next.

    Reported rather than silently switched: the same counter drives the
    head's decision that an inbound is dead for everyone, and an egress
    that quietly moved on would withhold evidence it is uniquely placed to
    give — it is the one client the head can always ask.
    """
    body = _head("/api/v1/egress/report-failure")
    if body is None:
        return None
    log.info(
        "нода отмечена нерабочей (inbound_declared_dead=%s), выдан конфиг на %s",
        body.get("inbound_declared_dead"),
        body.get("node_country", "?"),
    )
    return body["vless_url"]


# --- running xray -----------------------------------------------------------


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


def start_xray(vless_url: str) -> tuple[subprocess.Popen, str]:
    config = build_config(parse_vless(vless_url), SOCKS_PORT)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(config, handle)
        path = handle.name

    process = subprocess.Popen([XRAY, "run", "-c", path], stdout=subprocess.DEVNULL)
    link = parse_vless(vless_url)
    log.info("выход через %s:%s (%s), SOCKS5 на %s", link["host"], link["port"], link["network"], SOCKS_PORT)
    return process, path


def traffic_flows() -> bool:
    """Does anything actually get through right now?

    Checked against the destination that matters rather than a generic
    connectivity endpoint: a node that is up and reachable but cannot
    itself reach Telegram is useless for the one job this proxy has, and a
    check that passed anyway would keep the bot silent while reporting
    health.
    """
    try:
        with httpx.Client(proxy=f"socks5://127.0.0.1:{SOCKS_PORT}", timeout=20.0) as client:
            client.get(PROBE_URL)
        return True
    except (httpx.HTTPError, OSError) as exc:
        # httpx wraps a refused SOCKS handshake as ProxyError, a dead node
        # as ConnectTimeout, and a stalled one as ReadTimeout — all of them
        # mean the same thing here, and the distinction goes to the log
        # rather than into a decision.
        log.warning("проба не прошла: %s: %s", type(exc).__name__, exc)
        return False


def _stop(process: subprocess.Popen, config_path: str) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
    with contextlib.suppress(OSError):
        os.unlink(config_path)


def main() -> int:
    if not os.access(XRAY, os.X_OK):
        return _misconfigured(
            f"нет исполняемого файла xray по пути {XRAY}",
            "Этот контейнер собирается из образа головы, где xray уже есть.",
            "Если образ свой — укажите путь в XRAY_BINARY.",
        )

    pinned = os.environ.get("EGRESS_VLESS_URL", "").strip()
    if pinned:
        try:
            parse_vless(pinned)
        except ValueError as exc:
            return _misconfigured(f"не разобрать EGRESS_VLESS_URL: {exc}")
        log.info("закреплён фиксированный сервер, переключаться не буду")
    elif not ADMIN_TOKEN:
        return _misconfigured(
            "нечем работать: не задан ни EGRESS_VLESS_URL, ни ADMIN_API_TOKEN.",
            "Обычно нужен второй — тогда конфиг берётся у головы и меняется",
            "сам, когда нода перестаёт работать.",
        )

    current = pinned or None
    process: subprocess.Popen | None = None
    config_path = ""
    failures = 0

    while True:
        if current is None:
            current = fetch_config()
            if current is None:
                # Голова может подниматься дольше нас, или нод ещё нет.
                # И то и другое проходит само, поэтому ждём, а не падаем.
                time.sleep(30)
                continue

        if process is None:
            try:
                process, config_path = start_xray(current)
            except ValueError as exc:
                log.error("голова выдала ссылку, которую не разобрать: %s", exc)
                current = None
                time.sleep(30)
                continue
            failures = 0

        time.sleep(PROBE_SECONDS)

        if process.poll() is not None:
            log.warning("xray завершился с кодом %s, перезапускаю", process.returncode)
            _stop(process, config_path)
            process = None
            continue

        if traffic_flows():
            failures = 0
            continue

        failures += 1
        log.warning("проба не прошла %s раз подряд из %s", failures, PROBE_FAILURES)
        if failures < PROBE_FAILURES:
            continue

        if pinned:
            # Переключаться некуда: сервер задан явно. Перезапуск — всё,
            # что осталось, и он лечит зависший xray, но не блокировку.
            log.error("закреплённый сервер не отвечает; перезапускаю xray")
            _stop(process, config_path)
            process = None
            failures = 0
            continue

        log.error("нода не несёт трафик — прошу у головы другую")
        replacement = report_failure()
        _stop(process, config_path)
        process = None
        failures = 0
        current = replacement  # None → возьмём свежий на следующем круге


if __name__ == "__main__":
    raise SystemExit(main())
