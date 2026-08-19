"""The bot's door into the same two operations the app uses.

This file exists because of a hole rather than a feature. `/api/v1/connect`
and `/api/v1/me/connect` were implemented twice, and the check that decides
whether a user has paid for their hour was added to one of them. So the app
could not connect without watching an advertisement, while the same
operation one route over handed a working config to anyone holding the
service token.

Which is everyone: the service token ships inside the APK, and unzipping
one yields it. The bypass was three calls — take the token, create an
account through /auth/device, call /connect — and the service was free and
unlimited forever.

None of the 292 tests caught it, because every one of them went through the
door that had the check.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.config import get_settings
from app.db import models
from app.db.base import Base
from app.main import app
from app.services import access
from tests.factories import make_inbound, make_node, make_user

SERVICE = "test-service-token"
ADMIN = "test-admin-token"


@pytest.fixture
def session_factory(monkeypatch):
    monkeypatch.setenv("HEAD_SECRET_KEY", SERVICE)
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
    with TestClient(app, headers={"X-Admin-Token": ADMIN, "X-Service-Token": SERVICE}) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _node_with_room(db):
    node = make_node(db)
    make_inbound(db, node)
    make_inbound(db, node, port=8443)
    db.commit()
    return node


# --- the hole ------------------------------------------------------------


def test_paid_time_is_required_even_through_the_bot_s_door(client, db):
    """The regression. This route was implemented separately from
    /me/connect and ended up without the access check, so it handed out
    configs to anyone who could reach it."""
    _node_with_room(db)
    user = make_user(db)
    db.commit()

    response = client.post("/api/v1/connect", json={"user_id": str(user.id)})

    assert response.status_code == 402


def test_reporting_a_failure_needs_paid_time_too(client, db):
    """Same door, same bypass: a config swap is a config."""
    _node_with_room(db)
    user = make_user(db)
    db.commit()

    response = client.post("/api/v1/report-failure", json={"user_id": str(user.id)})

    assert response.status_code == 402


def test_registering_an_account_does_not_grant_anything(client, db):
    """The first step of the bypass. Accounts are free; time is not."""
    _node_with_room(db)
    registration = client.post("/api/v1/auth/device", json={}).json()

    refused = client.post("/api/v1/connect", json={"user_id": registration["user_id"]})

    assert refused.status_code == 402


# --- and it still works for the caller it exists for ---------------------


def test_a_user_with_time_connects_through_this_door(client, db):
    _node_with_room(db)
    user = make_user(db)
    access.grant_manual(db, user, 60, by="test")
    db.commit()

    response = client.post("/api/v1/connect", json={"user_id": str(user.id)})

    assert response.status_code == 200, response.text
    assert response.json()["vless_url"].startswith("vless://")


def test_the_bot_flow_works_end_to_end(client, db):
    """Grant, then connect — what the operator's console actually does."""
    _node_with_room(db)
    user = make_user(db)
    db.commit()

    granted = client.post("/api/v1/admin/grant-access", json={"user_id": str(user.id)})
    assert granted.status_code == 200
    assert granted.json()["access_active"] is True

    connected = client.post("/api/v1/connect", json={"user_id": str(user.id)})
    assert connected.status_code == 200


def test_both_doors_refuse_and_admit_alike(client, db):
    """The two routes must not drift again: same user, same answer."""
    _node_with_room(db)
    registration = client.post("/api/v1/auth/device", json={}).json()
    user_id, token = registration["user_id"], registration["token"]
    authed = {"Authorization": f"Bearer {token}", "X-Service-Token": SERVICE}

    assert client.post("/api/v1/connect", json={"user_id": user_id}).status_code == 402
    assert client.post("/api/v1/me/connect", headers=authed).status_code == 402

    access.grant_manual(db, db.get(models.User, __import__("uuid").UUID(user_id)), 60, by="t")
    db.commit()

    assert client.post("/api/v1/connect", json={"user_id": user_id}).status_code == 200
    assert client.post("/api/v1/me/connect", headers=authed).status_code == 200


def test_an_unknown_user_is_still_a_404(client, db):
    _node_with_room(db)
    import uuid

    response = client.post("/api/v1/connect", json={"user_id": str(uuid.uuid4())})

    assert response.status_code == 404


def test_the_route_needs_the_admin_token(db):
    """It names a user by id, so it acts on somebody else's behalf — which
    is exactly what the APK's token must never be enough for."""
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app, headers={"X-Service-Token": SERVICE}) as apk:
        assert apk.post("/api/v1/connect", json={"user_id": "x"}).status_code == 401
    app.dependency_overrides.clear()
