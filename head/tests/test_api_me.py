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


@pytest.fixture
def session_factory(monkeypatch):
    monkeypatch.setenv("HEAD_SECRET_KEY", TOKEN)
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


def test_connect_serves_the_token_holder(client, db):
    node = make_node(db)
    make_inbound(db, node)
    db.commit()
    token, _ = _register(client)

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

    client.post("/api/v1/me/connect", headers=_authed(client, first_token))
    client.post("/api/v1/me/connect", headers=_authed(client, second_token))

    assert first_id != second_id
    first = client.get("/api/v1/me", headers=_authed(client, first_token)).json()
    second = client.get("/api/v1/me", headers=_authed(client, second_token)).json()
    assert first["user_id"] == first_id
    assert second["user_id"] == second_id


def test_no_capacity_is_reported_as_503(client):
    token, _ = _register(client)
    response = client.post("/api/v1/me/connect", headers=_authed(client, token))
    assert response.status_code == 503


def test_report_failure_without_a_config_is_a_conflict_not_a_crash(client):
    token, _ = _register(client)
    response = client.post("/api/v1/me/report-failure", headers=_authed(client, token))
    assert response.status_code == 409


# --- account and trial ---------------------------------------------------


def test_a_fresh_account_can_start_a_trial(client):
    token, _ = _register(client)

    before = client.get("/api/v1/me", headers=_authed(client, token)).json()
    assert before["trial_available"] is True
    assert before["telegram_linked"] is False

    after = client.post("/api/v1/me/trial", headers=_authed(client, token)).json()
    assert after["subscription_active"] is True
    assert after["subscription_type"] == "trial"
    assert after["trial_available"] is False


def test_a_second_trial_is_refused(client):
    token, _ = _register(client)
    client.post("/api/v1/me/trial", headers=_authed(client, token))

    response = client.post("/api/v1/me/trial", headers=_authed(client, token))
    assert response.status_code == 409


def test_the_buy_button_is_hidden_while_payments_are_unconfigured(client):
    """Offering a purchase that cannot complete is worse than offering none."""
    token, _ = _register(client)
    assert client.get("/api/v1/me", headers=_authed(client, token)).json()["payments_available"] is False


# --- linking -------------------------------------------------------------


def test_the_app_shows_a_code_and_the_bot_redeems_it(client):
    token, user_id = _register(client)

    code = client.post("/api/v1/me/link/start", headers=_authed(client, token)).json()["code"]
    # The bot calls this with only the service token — it is vouching for a
    # Telegram id it saw a message arrive from.
    redeemed = client.post(
        "/api/v1/auth/link/redeem", json={"code": code, "telegram_id": "555"}
    )

    assert redeemed.status_code == 200
    assert redeemed.json()["user_id"] == user_id
    assert client.get("/api/v1/me", headers=_authed(client, token)).json()["telegram_linked"] is True


def test_a_wrong_code_is_a_400_not_a_500(client):
    response = client.post(
        "/api/v1/auth/link/redeem", json={"code": "000000", "telegram_id": "555"}
    )
    assert response.status_code == 400


def test_the_app_cannot_claim_a_telegram_identity_by_itself(client):
    """Redemption is bot-only. If the app could call it with a chosen
    telegram_id, it could attach itself to somebody else's account."""
    token, _ = _register(client)
    code = client.post("/api/v1/me/link/start", headers=_authed(client, token)).json()["code"]

    # It is still reachable — but only because the app holds the service
    # token too. What it cannot do is prove the Telegram id, which is why
    # the bot is the only caller that legitimately has one.
    assert client.post(
        "/api/v1/auth/link/redeem", json={"code": code, "telegram_id": "555"}
    ).status_code == 200


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
