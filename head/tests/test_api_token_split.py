"""What the APK's token can and cannot reach.

The service token ships inside the Android app. Anyone who unzips one has
it — that is not a flaw to fix but a fact to design around, and for a while
it was not designed around at all. With that token alone it was possible to
grant oneself unlimited access, redeem an ad nobody watched, register a
rogue node and be handed every user's traffic, read Telegram ids next to
working vless:// links, and restart the whole fleet.

So this file asserts the boundary itself rather than any one endpoint: what
the app may reach with its public token, and that everything else refuses
it. Adding a privileged endpoint without the admin token now fails here.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.config import get_settings
from app.db import models  # noqa: F401 - importing registers every table
from app.db.base import Base
from app.main import app
from tests.factories import make_user

SERVICE = "public-token-that-ships-in-the-apk"
ADMIN = "server-side-only-token"

#: Everything the app is allowed to reach with the service token alone.
#: Deliberately short. Nothing here acts on another account's behalf.
APP_SURFACE = {
    ("POST", "/api/v1/auth/device"),
    ("GET", "/api/v1/me"),
    ("POST", "/api/v1/me/connect"),
    ("POST", "/api/v1/me/report-failure"),
    ("POST", "/api/v1/me/ad/prepare"),
    ("POST", "/api/v1/me/ad/complete"),
    ("POST", "/api/v1/me/ad/unavailable"),
    ("POST", "/api/v1/me/link/start"),
    ("GET", "/api/v1/routing-policy"),
}


@pytest.fixture
def session_factory(monkeypatch):
    monkeypatch.setenv("HEAD_SECRET_KEY", SERVICE)
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN)
    monkeypatch.setenv("BACKGROUND_JOBS_ENABLED", "false")
    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, future=True)
    get_settings.cache_clear()


@pytest.fixture
def db(session_factory) -> Session:
    with session_factory() as session:
        yield session


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _privileged_routes() -> list[tuple[str, str]]:
    """Every route that is not the app's surface, from the live schema."""
    routes = []
    for path, ops in app.openapi()["paths"].items():
        if path == "/health":
            continue
        for method in ops:
            pair = (method.upper(), path)
            if pair not in APP_SURFACE:
                routes.append(pair)
    return sorted(routes)


# --- the boundary --------------------------------------------------------


def test_the_apk_token_opens_nothing_privileged(client):
    """The regression, stated as a rule rather than a list.

    A new privileged endpoint that forgets the admin token fails here
    without anyone remembering to add a test for it.
    """
    leaking = []
    for method, path in _privileged_routes():
        url = path.replace("{node_id}", "00000000-0000-0000-0000-000000000000")
        response = client.request(
            method, url, headers={"X-Service-Token": SERVICE}, json={}
        )
        if response.status_code != 401:
            leaking.append(f"{method} {path} -> {response.status_code}")

    assert leaking == [], "reachable with the token that ships in the APK:\n" + "\n".join(leaking)


def test_the_admin_token_does_not_substitute_for_the_service_one(client):
    """They are different secrets for different jobs, not a hierarchy."""
    response = client.post("/api/v1/auth/device", headers={"X-Admin-Token": ADMIN}, json={})
    assert response.status_code == 401


def test_the_app_surface_works_with_the_service_token(client):
    response = client.post("/api/v1/auth/device", headers={"X-Service-Token": SERVICE}, json={})
    assert response.status_code == 201


# --- the specific bypasses that existed ----------------------------------


def test_granting_access_needs_the_admin_token(client, db):
    """Was: register an account, grant yourself an hour, repeat forever."""
    user = make_user(db)
    db.commit()
    body = {"user_id": str(user.id)}

    assert client.post("/api/v1/admin/grant-access", headers={"X-Service-Token": SERVICE}, json=body).status_code == 401
    assert client.post("/api/v1/admin/grant-access", headers={"X-Admin-Token": ADMIN}, json=body).status_code == 200


def test_redeeming_an_ad_token_needs_the_admin_token(client):
    """Was: ask /me/ad/prepare for a token, hand it straight to /ad/verify,
    and be credited for an advertisement nobody played."""
    response = client.post(
        "/api/v1/ad/verify", headers={"X-Service-Token": SERVICE}, json={"nonce": "x"}
    )
    assert response.status_code == 401


def test_registering_a_node_needs_the_admin_token(client):
    """Was: add a server you control to the pool and be handed real users."""
    response = client.post(
        "/api/v1/nodes/register", headers={"X-Service-Token": SERVICE}, json={}
    )
    assert response.status_code == 401


def test_reading_pending_pushes_needs_the_admin_token(client):
    """Was: enumerate Telegram ids alongside working vless:// links."""
    response = client.get("/api/v1/pushes/pending", headers={"X-Service-Token": SERVICE})
    assert response.status_code == 401


def test_approving_an_update_needs_the_admin_token(client):
    """Was: restart every node in the fleet."""
    response = client.post(
        "/api/v1/xray-updates/decide",
        headers={"X-Service-Token": SERVICE},
        json={"target_version": "1.0.0", "approve": True},
    )
    assert response.status_code == 401


# --- refusing to run misconfigured ---------------------------------------


def test_an_unset_admin_token_refuses_rather_than_admits(client, monkeypatch):
    """Fails closed. A blank secret that let everyone through would be the
    hole nobody notices."""
    monkeypatch.setenv("ADMIN_API_TOKEN", "")
    get_settings.cache_clear()

    response = client.get("/api/v1/pushes/pending", headers={"X-Admin-Token": ""})
    assert response.status_code == 500


def test_the_default_admin_token_refuses_too(client, monkeypatch):
    monkeypatch.setenv("ADMIN_API_TOKEN", "change-me")
    get_settings.cache_clear()

    response = client.get("/api/v1/pushes/pending", headers={"X-Admin-Token": "change-me"})
    assert response.status_code == 500
