"""The API surface the Android app talks to, over real HTTP.

The point of these tests is the boundary, not the logic underneath: that a
bearer token is genuinely required, that it scopes every call to its own
account, and that the app cannot reach past it by naming a user_id the way
the bot does.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.config import get_settings
from app.db import models  # noqa: F401 - registers tables
from app.db.base import Base
from app.db.models.user import User, UserStatus
from app.main import app
from tests.factories import make_inbound, make_node

TOKEN = "test-service-token"
ADMIN = "test-admin-token"


@pytest.fixture
def session_factory(monkeypatch):
    monkeypatch.setenv("HEAD_SECRET_KEY", TOKEN)
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN)
    monkeypatch.setenv("BACKGROUND_JOBS_ENABLED", "false")
    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, future=True)
    get_settings.cache_clear()


@pytest.fixture
def db(session_factory) -> Session:
    with session_factory() as session:
        yield session


@pytest.fixture
def client(db, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr("app.services.config_selector.push_node_config", lambda _db, _n: None)
    with TestClient(app, headers={"X-Service-Token": TOKEN}) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _register(client) -> tuple[str, str]:
    response = client.post("/api/v1/auth/device", json={"device_label": "Pixel 7"})
    assert response.status_code == 201, response.text
    body = response.json()
    return body["token"], body["user_id"]


def _authed(client, token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Service-Token": TOKEN}


def _watch_ad(client, token: str, package: str = "hour") -> dict:
    """The full round trip a real client makes to buy time.

    Returns the last progress payload, whose `account` is the fresh state.
    """
    ticket = client.post(
        "/api/v1/me/ad/prepare", json={"package": package}, headers=_authed(client, token)
    )
    assert ticket.status_code == 200, ticket.text
    body = ticket.json()

    last = None
    for _ in range(body["views_required"]):
        done = client.post(
            "/api/v1/me/ad/complete",
            json={"nonce": body["nonce"]},
            headers=_authed(client, token),
        )
        assert done.status_code == 200, done.text
        last = done.json()
    return last


# --- registration --------------------------------------------------------


def test_first_launch_gets_an_account_without_asking_anything(client):
    """The product is one button. A registration form before the first
    connection would be friction that buys the user nothing."""
    response = client.post("/api/v1/auth/device", json={})
    assert response.status_code == 201
    assert response.json()["token"].startswith("fsv1_")


def test_the_account_endpoint_needs_a_bearer_token(client):
    assert client.get("/api/v1/me").status_code == 401


def test_a_garbage_bearer_token_is_rejected(client):
    response = client.get("/api/v1/me", headers=_authed(client, "fsv1_nope"))
    assert response.status_code == 401


def test_a_malformed_authorization_header_is_rejected(client):
    response = client.get(
        "/api/v1/me", headers={"Authorization": "Basic abc", "X-Service-Token": TOKEN}
    )
    assert response.status_code == 401


def test_the_service_token_alone_is_not_enough(client):
    """This is the whole reason the layer exists: the service token ships
    inside the APK, so on its own it must open nothing."""
    assert client.get("/api/v1/me", headers={"X-Service-Token": TOKEN}).status_code == 401


def test_a_banned_account_gets_403_not_401(client, db):
    """401 invites a client to retry or re-authenticate; nothing about
    banning is fixed by trying again."""
    token, user_id = _register(client)
    db.get(User, __import__("uuid").UUID(user_id)).status = UserStatus.banned
    db.commit()

    assert client.get("/api/v1/me", headers=_authed(client, token)).status_code == 403


# --- connect -------------------------------------------------------------


def test_connect_is_refused_until_an_ad_is_watched(client, db):
    """The business model, as an assertion. 402 rather than 403 — the
    client's move is to show an ad, not to re-authenticate, and the two
    must not look alike to it."""
    node = make_node(db)
    make_inbound(db, node)
    db.commit()
    token, _ = _register(client)

    response = client.post("/api/v1/me/connect", headers=_authed(client, token))

    assert response.status_code == 402


def test_connect_serves_the_token_holder_after_an_ad(client, db):
    node = make_node(db)
    make_inbound(db, node)
    db.commit()
    token, _ = _register(client)
    _watch_ad(client, token)

    response = client.post("/api/v1/me/connect", headers=_authed(client, token))

    assert response.status_code == 200, response.text
    assert response.json()["vless_url"].startswith("vless://")


def test_connect_without_a_token_is_refused(client, db):
    node = make_node(db)
    make_inbound(db, node)
    db.commit()

    assert client.post("/api/v1/me/connect").status_code == 401


def test_two_apps_get_their_own_configs(client, db):
    node = make_node(db)
    make_inbound(db, node)
    make_inbound(db, node, port=8443)
    db.commit()
    first_token, first_id = _register(client)
    second_token, second_id = _register(client)
    _watch_ad(client, first_token)
    _watch_ad(client, second_token)

    client.post("/api/v1/me/connect", headers=_authed(client, first_token))
    client.post("/api/v1/me/connect", headers=_authed(client, second_token))

    assert first_id != second_id
    first = client.get("/api/v1/me", headers=_authed(client, first_token)).json()
    second = client.get("/api/v1/me", headers=_authed(client, second_token)).json()
    assert first["user_id"] == first_id
    assert second["user_id"] == second_id


def test_no_capacity_is_reported_as_503(client):
    """Distinct from 402: the user did their part, we have no room."""
    token, _ = _register(client)
    _watch_ad(client, token)
    response = client.post("/api/v1/me/connect", headers=_authed(client, token))
    assert response.status_code == 503


def test_report_failure_without_a_config_is_a_conflict_not_a_crash(client):
    token, _ = _register(client)
    _watch_ad(client, token)
    response = client.post("/api/v1/me/report-failure", headers=_authed(client, token))
    assert response.status_code == 409


# --- buying an hour ------------------------------------------------------


def test_a_fresh_account_has_no_access(client):
    token, _ = _register(client)

    body = client.get("/api/v1/me", headers=_authed(client, token)).json()

    assert body["access_active"] is False
    assert body["access_seconds_remaining"] == 0


def test_a_completed_ad_buys_an_hour(client):
    token, _ = _register(client)

    account = _watch_ad(client, token)["account"]

    assert account["access_active"] is True
    assert 3500 < account["access_seconds_remaining"] <= 3600
    assert account["access_is_grace"] is False


def test_the_packages_are_offered_by_the_server(client):
    """Served rather than compiled into the app, so the offer can change
    without a store release."""
    token, _ = _register(client)

    packages = client.get("/api/v1/me", headers=_authed(client, token)).json()["packages"]

    by_code = {p["code"]: p for p in packages}
    assert by_code["short"]["total_minutes"] == 15
    assert by_code["short"]["ad_kind"] == "interstitial"
    assert by_code["hour"]["total_minutes"] == 60
    assert by_code["double"]["total_minutes"] == 120
    assert by_code["double"]["views"] == 2


def test_the_short_package_buys_fifteen_minutes(client):
    token, _ = _register(client)

    account = _watch_ad(client, token, "short")["account"]

    assert 800 < account["access_seconds_remaining"] <= 900


def test_the_two_hour_package_reports_progress_between_videos(client):
    """The app needs to say "1 of 2" rather than looking stuck."""
    token, _ = _register(client)
    ticket = client.post(
        "/api/v1/me/ad/prepare", json={"package": "double"}, headers=_authed(client, token)
    ).json()

    first = client.post(
        "/api/v1/me/ad/complete", json={"nonce": ticket["nonce"]},
        headers=_authed(client, token),
    ).json()

    assert first["views_done"] == 1
    assert first["views_required"] == 2
    assert first["complete"] is False
    # And the hour is already theirs, before the second video.
    assert 3500 < first["account"]["access_seconds_remaining"] <= 3600


def test_a_package_the_server_does_not_offer_is_refused(client):
    """The reward is a server-side decision — a client able to name its own
    would name a large one."""
    token, _ = _register(client)

    response = client.post(
        "/api/v1/me/ad/prepare", json={"package": "one_year"}, headers=_authed(client, token)
    )

    assert response.status_code == 400


def test_a_token_cannot_be_spent_twice_over_http(client):
    """Without this, one recorded call is an unlimited access generator."""
    token, _ = _register(client)
    ticket = client.post(
        "/api/v1/me/ad/prepare", json={"package": "hour"}, headers=_authed(client, token)
    ).json()
    client.post(
        "/api/v1/me/ad/complete", json={"nonce": ticket["nonce"]},
        headers=_authed(client, token),
    )

    again = client.post(
        "/api/v1/me/ad/complete", json={"nonce": ticket["nonce"]},
        headers=_authed(client, token),
    )

    assert again.status_code == 400


def test_one_account_cannot_redeem_anothers_token(client):
    mine, _ = _register(client)
    theirs, _ = _register(client)
    ticket = client.post(
        "/api/v1/me/ad/prepare", json={"package": "hour"}, headers=_authed(client, mine)
    ).json()

    stolen = client.post(
        "/api/v1/me/ad/complete", json={"nonce": ticket["nonce"]},
        headers=_authed(client, theirs),
    )

    assert stolen.status_code == 400


def test_a_made_up_token_is_refused(client):
    token, _ = _register(client)
    response = client.post(
        "/api/v1/me/ad/complete", json={"nonce": "invented"}, headers=_authed(client, token)
    )
    assert response.status_code == 400


def test_the_fallback_lets_someone_online_when_no_ad_could_be_shown(client):
    """A bad fill rate must not be a total outage — a VPN that will not
    connect is not a degraded VPN."""
    token, _ = _register(client)

    body = client.post("/api/v1/me/ad/unavailable", headers=_authed(client, token)).json()

    assert body["access_active"] is True
    assert body["access_is_grace"] is True


def test_the_fallback_cannot_be_farmed(client):
    token, _ = _register(client)
    client.post("/api/v1/me/ad/unavailable", headers=_authed(client, token))

    again = client.post("/api/v1/me/ad/unavailable", headers=_authed(client, token))

    assert again.status_code == 429


def test_the_fallback_still_lets_the_user_connect(client, db):
    node = make_node(db)
    make_inbound(db, node, port=8443)
    db.commit()
    token, _ = _register(client)
    client.post("/api/v1/me/ad/unavailable", headers=_authed(client, token))

    assert client.post("/api/v1/me/connect", headers=_authed(client, token)).status_code == 200


# --- linking -------------------------------------------------------------


def test_the_app_shows_a_code_and_the_bot_redeems_it(client):
    token, user_id = _register(client)

    code = client.post("/api/v1/me/link/start", headers=_authed(client, token)).json()["code"]
    # The bot redeems it, holding the admin token: it is vouching for a
    # Telegram id it saw a message arrive from, which is a claim only
    # something running on our own server can make.
    redeemed = client.post(
        "/api/v1/auth/link/redeem",
        json={"code": code, "telegram_id": "555"},
        headers={"X-Admin-Token": ADMIN},
    )

    assert redeemed.status_code == 200
    assert redeemed.json()["user_id"] == user_id
    assert client.get("/api/v1/me", headers=_authed(client, token)).json()["telegram_linked"] is True


def test_a_wrong_code_is_a_400_not_a_500(client):
    response = client.post(
        "/api/v1/auth/link/redeem",
        json={"code": "000000", "telegram_id": "555"},
        headers={"X-Admin-Token": ADMIN},
    )
    assert response.status_code == 400


def test_the_app_cannot_claim_a_telegram_identity_by_itself(client):
    """Redemption is bot-only, and now actually is.

    The app holds a code — its own — and could previously call this route,
    because it carried the same token the bot did. Naming any telegram_id it
    liked would have attached it to that person's account. It is behind the
    admin token now, which the app never receives.
    """
    token, _ = _register(client)
    code = client.post("/api/v1/me/link/start", headers=_authed(client, token)).json()["code"]

    refused = client.post(
        "/api/v1/auth/link/redeem",
        json={"code": code, "telegram_id": "555"},
        headers=_authed(client, token),
    )

    assert refused.status_code == 401


# --- routing policy ------------------------------------------------------


def test_the_routing_policy_lists_the_russian_internet_as_direct(client):
    token, _ = _register(client)

    policy = client.get("/api/v1/routing-policy", headers=_authed(client, token)).json()

    assert "ru" in policy["direct_tlds"]
    assert "рф" in policy["direct_tlds"]
    assert "vk.com" in policy["direct_domains"]
    assert "ru.sberbankmobile" in policy["direct_packages"]
    # Without this the local network disappears the moment the tunnel is up.
    assert "private" in policy["direct_geoip"]


def test_the_routing_policy_needs_a_token(client):
    assert client.get("/api/v1/routing-policy").status_code == 401
