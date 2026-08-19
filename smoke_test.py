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
"""

from __future__ import annotations

import argparse
import sys
import uuid

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

        def connect():
            if not nodes or not any(n["status"] == "active" for n in nodes):
                return (SKIP, "no active node registered yet — add one in the admin panel first")
            response = api.post("/api/v1/connect", json={"user_id": user_id})
            if response.status_code == 503:
                return (FAIL, f"no capacity: {response.json().get('detail')}")
            assert response.status_code == 200, f"{response.status_code}: {response.text[:200]}"
            url = response.json()["vless_url"]
            assert url.startswith("vless://"), f"unexpected config: {url[:60]}"
            return f"got a config on {response.json()['node_country']}"

        check("issue a VPN config", connect)

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
