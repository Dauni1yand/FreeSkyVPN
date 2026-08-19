#!/usr/bin/env python3
"""Post-deploy smoke test: exercise a running stack over HTTP.

The unit tests prove the logic; this proves the *deployment* — that the
containers are up, migrations ran, the admin panel answers, the service
both tokens are accepted where they should be, and a real user flow works
end to end.
Those are exactly the things that pass in CI and still break on a server.

    python3 smoke_test.py --url http://127.0.0.1:8000 \
        --token "$HEAD_SECRET_KEY" --admin-token "$ADMIN_API_TOKEN"

Add --admin-user/--admin-password to check the panel too. Nothing here
writes to a node; the connect check is skipped when no node is registered
yet, and reported as skipped rather than passed.

--deep adds the checks that cost time and prove the parts nothing else
does: that a config actually carries traffic out through the node, that a
watched ad turns into the right number of minutes, that time already paid
for does not ask for another ad, and that Telegram is reachable by whatever
route this deployment is configured to use. Run it from inside the head
container, where xray and the settings already are:

    docker compose exec head python smoke_test.py --deep \
        --token "$HEAD_SECRET_KEY" --admin-token "$ADMIN_API_TOKEN"
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import closing

import httpx

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    mark = {PASS: "\033[32m✓\033[0m", FAIL: "\033[31m✗\033[0m", SKIP: "\033[33m–\033[0m"}[status]
    print(f" {mark} {name}" + (f"  — {detail}" if detail else ""))


def check(name: str, fn) -> object:
    try:
        value = fn()
    except Exception as exc:  # a smoke test reports failures, it does not raise
        record(FAIL, name, f"{type(exc).__name__}: {exc}")
        return None
    if isinstance(value, tuple) and len(value) == 2 and value[0] in (PASS, FAIL, SKIP):
        record(value[0], name, value[1])
        return None
    record(PASS, name, str(value) if value else "")
    return value


# --- deep checks ------------------------------------------------------------
#
# Everything above answers "is the deployment wired up". These answer "does
# it do the thing", which is a different question and the one that actually
# matters: a stack can pass every check above and still hand out configs
# that carry no traffic.


def _free_port() -> int:
    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _load_egress_module():
    """Reuse the proxy container's config builder rather than a second copy.

    It already turns a vless:// link into an Xray client config, and it is
    covered by head/tests/test_egress_config.py. A private copy here would
    be the same code with no tests and its own drift.
    """
    for candidate in ("/provisioning/egress.py", "provisioning/egress.py"):
        if os.path.exists(candidate):
            spec = importlib.util.spec_from_file_location("egress", candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


class _Tunnel:
    """A local xray client speaking the config a user would be handed."""

    def __init__(self, vless_url: str):
        self._url = vless_url
        self.port = _free_port()
        self._process: subprocess.Popen | None = None
        self._config: str | None = None

    def __enter__(self):
        egress = _load_egress_module()
        if egress is None:
            raise RuntimeError("provisioning/egress.py not found — run this inside the head container")

        xray = os.environ.get("XRAY_CLIENT_BINARY_PATH", "/usr/local/bin/xray")
        if not os.access(xray, os.X_OK):
            raise RuntimeError(f"no xray at {xray}")

        config = egress.build_config(egress.parse_vless(self._url), self.port)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(config, handle)
            self._config = handle.name

        self._process = subprocess.Popen(
            [xray, "run", "-c", self._config],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        # Wait for the inbound rather than sleeping a fixed amount: Reality
        # handshakes vary, and a fixed sleep is either slow or flaky.
        deadline = time.time() + 15
        while time.time() < deadline:
            if self._process.poll() is not None:
                err = (self._process.stderr.read() or b"").decode()[-300:]
                raise RuntimeError(f"xray exited immediately: {err}")
            with closing(socket.socket()) as probe:
                probe.settimeout(0.3)
                if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                    return self
            time.sleep(0.3)
        raise RuntimeError("xray did not open its socks port within 15s")

    def __exit__(self, *_exc):
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        if self._config:
            os.unlink(self._config)


def _public_ip(proxy: str | None) -> str:
    with httpx.Client(proxy=proxy, timeout=20.0) as client:
        return client.get("https://api.ipify.org").text.strip()



def _deep_checks(api, args, device_token, issued_url) -> None:
    """The checks that cost seconds and answer the question that matters."""

    # --- traffic actually leaves through the node --------------------------
    #
    # Everything else can pass while this fails: the head hands out a link,
    # the node reports healthy, and not one packet crosses. Nothing short of
    # dialling the config and watching an address change proves otherwise.

    def tunnel_carries_traffic():
        if not issued_url:
            return (SKIP, "no config was issued, nothing to dial")

        try:
            direct_ip = _public_ip(None)
        except Exception:
            direct_ip = None  # a head behind a strict egress cannot self-locate

        with _Tunnel(issued_url) as tunnel:
            through_ip = _public_ip(f"socks5://127.0.0.1:{tunnel.port}")

        assert through_ip, "no address came back through the tunnel"
        if direct_ip and through_ip == direct_ip:
            return (
                FAIL,
                f"traffic left from the head's own address ({through_ip}) — "
                "the tunnel is not carrying it",
            )
        if direct_ip:
            return f"exits at {through_ip}, head is {direct_ip}"
        return f"exits at {through_ip} (head could not check its own address)"

    check("the tunnel carries traffic", tunnel_carries_traffic)

    # --- an ad turns into the right number of minutes ----------------------

    def ads_pay_out_per_view():
        if not device_token:
            return (SKIP, "no device token")
        auth = {"Authorization": f"Bearer {device_token}"}

        ticket = api.post("/api/v1/me/ad/prepare", json={"package": "double"}, headers=auth)
        assert ticket.status_code == 200, f"prepare: {ticket.status_code} {ticket.text[:140]}"
        ticket = ticket.json()
        assert ticket["views_required"] == 2, f"the two-hour package wants {ticket['views_required']} views"

        before = api.get("/api/v1/me", headers=auth).json()["access_seconds_remaining"]

        granted = []
        for view in range(ticket["views_required"]):
            done = api.post(
                "/api/v1/me/ad/complete", json={"nonce": ticket["nonce"]}, headers=auth
            )
            assert done.status_code == 200, f"view {view + 1}: {done.status_code} {done.text[:140]}"
            body = done.json()
            granted.append(body["minutes_granted"])
            # Credited per view, not per package: someone who watches one of
            # two videos has earned one hour, and owing them nothing until
            # the second is how you teach people to stop watching.
            assert body["complete"] == (view + 1 == ticket["views_required"])

        after = api.get("/api/v1/me", headers=auth).json()["access_seconds_remaining"]
        gained = (after - before) // 60
        expected = ticket["minutes_per_view"] * ticket["views_required"]
        assert gained >= expected - 1, f"watched {len(granted)} videos, got {gained} min, expected {expected}"
        return f"{len(granted)} videos → {gained} min"

    check("a watched ad pays out per view", ads_pay_out_per_view)

    # --- paid-for time is not charged twice --------------------------------

    def reconnect_costs_nothing():
        if not device_token:
            return (SKIP, "no device token")
        auth = {"Authorization": f"Bearer {device_token}"}
        first = api.post("/api/v1/me/connect", json={}, headers=auth)
        if first.status_code == 503:
            return (SKIP, "no capacity to hand out a config")
        assert first.status_code == 200, f"{first.status_code}: {first.text[:140]}"
        second = api.post("/api/v1/me/connect", json={}, headers=auth)
        assert second.status_code == 200, (
            f"reconnect asked for another ad ({second.status_code}) — "
            "time already bought must not be charged twice"
        )
        return "reconnects while time remains, no ad"

    check("paid time reconnects free", reconnect_costs_nothing)

    # --- the head can reach Telegram at all --------------------------------

    def telegram_is_reachable():
        try:
            from app.config import get_settings
        except Exception:
            return (SKIP, "run inside the head container to check this")

        settings = get_settings()
        if not settings.telegram_bot_token:
            return (SKIP, "no bot token configured")

        proxy = settings.telegram_proxy_url or None
        try:
            with httpx.Client(proxy=proxy, timeout=20.0) as client:
                response = client.get(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe"
                )
        except Exception as exc:
            route = f"through {proxy}" if proxy else "directly (no TELEGRAM_PROXY_URL set)"
            return (
                FAIL,
                f"cannot reach Telegram {route}: {type(exc).__name__}. "
                "In Russia this needs the egress container — see DEPLOY.md 10а",
            )

        if response.status_code != 200:
            detail = response.json().get("description", response.text[:80])
            return (FAIL, f"Telegram refused the token: {detail}")
        route = f"via {proxy}" if proxy else "directly"
        return f"@{response.json()['result']['username']} {route}"

    check("Telegram is reachable", telegram_is_reachable)

    # --- the schema is where the code expects it ---------------------------

    def migrations_are_current():
        try:
            from alembic.config import Config
            from alembic.runtime.migration import MigrationContext
            from alembic.script import ScriptDirectory

            from app.db.session import engine
        except Exception:
            return (SKIP, "run inside the head container to check this")

        for ini in ("/app/alembic.ini", "alembic.ini"):
            if os.path.exists(ini):
                script = ScriptDirectory.from_config(Config(ini))
                break
        else:
            return (SKIP, "alembic.ini not found")

        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
        expected = script.get_current_head()
        assert current == expected, f"database at {current}, code expects {expected}"
        return f"at {current}"

    check("migrations are current", migrations_are_current)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", required=True, help="HEAD_SECRET_KEY (ships in the APK)")
    parser.add_argument(
        "--admin-token",
        required=True,
        help="ADMIN_API_TOKEN (server-side only; the bot and provisioning use it)",
    )
    parser.add_argument("--admin-user")
    parser.add_argument("--admin-password")
    parser.add_argument(
        "--deep",
        action="store_true",
        help="also prove traffic flows, ads pay out, and Telegram is reachable",
    )
    args = parser.parse_args()

    api = httpx.Client(
        base_url=args.url.rstrip("/"),
        headers={"X-Service-Token": args.token, "X-Admin-Token": args.admin_token},
        timeout=30.0,
    )
    anon = httpx.Client(base_url=args.url.rstrip("/"), timeout=30.0, follow_redirects=False)

    print(f"\nFreeSkyVPN smoke test → {args.url}\n")

    print("service")

    def health():
        response = anon.get("/health")
        assert response.status_code == 200, f"status {response.status_code}"
        return "head is up"

    check("health endpoint answers", health)

    def auth_enforced():
        response = anon.post(
            "/api/v1/connect", json={"user_id": str(uuid.uuid4())}
        )
        if response.status_code == 500:
            return (FAIL, "head refuses to serve — HEAD_SECRET_KEY is unset or still the default")
        assert response.status_code == 401, f"expected 401 without a token, got {response.status_code}"
        return "unauthenticated calls are rejected"

    check("API requires a token", auth_enforced)

    def apk_token_opens_nothing_privileged():
        # The service token ships inside the APK, so anything it reaches is
        # reachable by anyone. This is the boundary that keeps that from
        # mattering — see head/app/api/auth.py.
        import httpx as _httpx

        with _httpx.Client(
            base_url=args.url.rstrip("/"),
            headers={"X-Service-Token": args.token},
            timeout=15.0,
        ) as apk:
            response = apk.get("/api/v1/pushes/pending")
        assert response.status_code == 401, (
            f"the APK's token reached an admin endpoint ({response.status_code}) — "
            "check ADMIN_API_TOKEN is set and differs from HEAD_SECRET_KEY"
        )
        return "admin endpoints refuse it"

    check("APK token opens nothing privileged", apk_token_opens_nothing_privileged)

    # Registering a device is the app's first call and the only one it can
    # make without a bearer token, so it doubles as the check that the
    # service token is right. The token it returns is what the rest of the
    # app surface needs.
    device_token = None

    def service_token_matches():
        nonlocal device_token
        response = api.post(
            "/api/v1/auth/device", json={"device_label": "smoke-test"}
        )
        assert response.status_code == 201, f"{response.status_code}: {response.text[:160]}"
        device_token = response.json()["token"]
        return "device registered"

    check("app token accepted", service_token_matches)

    def routing_policy_served():
        if not device_token:
            return (SKIP, "no device token to ask with")
        response = api.get(
            "/api/v1/routing-policy", headers={"Authorization": f"Bearer {device_token}"}
        )
        assert response.status_code == 200, f"{response.status_code}: {response.text[:120]}"
        policy = response.json()
        direct = len(policy["direct_tlds"]) + len(policy["direct_domains"])
        assert direct, "an empty policy sends Russian traffic through the tunnel"
        return f"{direct} direct rules, version {policy['version']}"

    check("split tunnel policy served", routing_policy_served)

    def admin_token_matches():
        # The bot's surface. A mismatch here is the usual reason a freshly
        # upgraded bot answers every button with an error.
        response = api.get("/api/v1/nodes")
        assert response.status_code != 401, "ADMIN_API_TOKEN does not match the head's"
        assert response.status_code == 200, f"status {response.status_code}: {response.text[:120]}"
        return "server-side token matches"

    check("admin token accepted", admin_token_matches)

    print("\ndata")

    nodes_response = api.get("/api/v1/nodes")
    nodes = nodes_response.json() if nodes_response.is_success else None
    record(PASS if nodes is not None else FAIL, "nodes readable",
           f"{len(nodes)} registered" if nodes is not None else nodes_response.text[:100])

    if nodes is not None:
        active = [n for n in nodes if n["status"] == "active"]
        free = [n for n in active if n["tier"] == "free"]
        paid = [n for n in active if n["tier"] == "paid"]
        isolated = [n for n in nodes if n["channel_state"] == "isolated"]

        record(
            PASS if active else FAIL,
            "an active node exists",
            f"{len(active)} active of {len(nodes)}" if nodes else "none registered",
        )
        if nodes:
            record(
                PASS if free else FAIL,
                "a free-tier node exists",
                "free users cannot connect without one" if not free else f"{len(free)}",
            )
            record(
                PASS if paid else SKIP,
                "a paid-tier node exists",
                "paying users fall back to shaped free nodes" if not paid else f"{len(paid)}",
            )
        if isolated:
            record(FAIL, "no isolated nodes", f"{len(isolated)} unreachable over both paths")
        elif nodes:
            record(PASS, "no isolated nodes")

    print("\nuser flow")

    telegram_id = 900_000_000 + uuid.uuid4().int % 10_000_000

    def register():
        response = api.post("/api/v1/auth/telegram", json={"telegram_id": telegram_id})
        assert response.status_code == 200, f"{response.status_code}: {response.text[:160]}"
        return response.json()["user_id"]

    user_id = check("register a throwaway user", register)

    if user_id:
        def grant():
            # There is no subscription to read any more: access is time
            # bought with a watched ad. The bot cannot show one, so this
            # exercises the same service-side grant the bot uses.
            response = api.post("/api/v1/admin/grant-access", json={"user_id": user_id})
            assert response.status_code == 200, f"{response.status_code}: {response.text[:160]}"
            data = response.json()
            assert data["access_active"], "grant did not put the account online"
            return f"{data['access_seconds_remaining'] // 60} min of access granted"

        check("grant access without an ad", grant)

        issued_url = None

        def connect():
            nonlocal issued_url
            if not nodes or not any(n["status"] == "active" for n in nodes):
                return (SKIP, "no active node registered yet — add one in the admin panel first")
            response = api.post("/api/v1/connect", json={"user_id": user_id})
            if response.status_code == 503:
                return (FAIL, f"no capacity: {response.json().get('detail')}")
            assert response.status_code == 200, f"{response.status_code}: {response.text[:200]}"
            url = response.json()["vless_url"]
            assert url.startswith("vless://"), f"unexpected config: {url[:60]}"
            issued_url = url
            return f"got a config on {response.json()['node_country']}"

        check("issue a VPN config", connect)

    if args.deep:
        print("\ndeep")
        _deep_checks(api, args, device_token, issued_url if user_id else None)

    if args.admin_user and args.admin_password:
        print("\nadmin panel")

        def admin_login():
            session = httpx.Client(base_url=args.url.rstrip("/"), timeout=30.0)
            response = session.post(
                "/admin/login",
                data={"username": args.admin_user, "password": args.admin_password},
                follow_redirects=True,
            )
            assert response.status_code == 200, f"status {response.status_code}"
            assert "Обзор" in response.text, "login did not reach the dashboard"
            return session

        try:
            session = admin_login()
            record(PASS, "admin login works", f"signed in as {args.admin_user}")
        except Exception as exc:
            record(FAIL, "admin login works", f"{type(exc).__name__}: {exc}")
            session = None

        if session:
            for path, label in [
                ("/admin/nodes", "nodes page"),
                ("/admin/users", "users page"),
                ("/admin/plans", "plans page"),
                ("/admin/sni", "SNI page"),
                ("/admin/events", "events page"),
            ]:
                check(
                    f"{label} renders",
                    lambda p=path: (
                        PASS if session.get(p).status_code == 200 else FAIL,
                        "" if session.get(p).status_code == 200 else "non-200",
                    ),
                )

            def secrets_configured():
                page = session.get("/admin/nodes").text
                if "SECRETS_KEY не задан" in page:
                    return (FAIL, "SECRETS_KEY unset — nodes cannot be added")
                return "SECRETS_KEY is set"

            check("node credentials can be stored", secrets_configured)

    failed = sum(1 for status, _, _ in results if status == FAIL)
    skipped = sum(1 for status, _, _ in results if status == SKIP)
    passed = sum(1 for status, _, _ in results if status == PASS)

    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    if failed:
        print("\nfailures:")
        for status, name, detail in results:
            if status == FAIL:
                print(f"  · {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
