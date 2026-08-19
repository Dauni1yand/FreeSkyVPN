"""The account the head's own proxy connects as.

The proxy that carries Telegram traffic dials one of our own nodes, and to
do that it has to be a user. Two things follow that are worth pinning: it
must be the *same* user every time, or each restart would leave another
orphaned account holding an inbound; and its access must not run out, or
the bot goes silent for a reason that points nowhere near here.

The endpoints are admin-only for a reason that is easy to undo by
accident: they hand out a working config with no advertisement watched,
which is precisely what the token shipped inside the APK must never reach.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.config import get_settings
from app.db import models  # noqa: F401 - registers tables
from app.db.base import Base
from app.db.models.user import AuthIdentity, User
from app.main import app
from app.services import egress
from tests.factories import make_inbound, make_node, seed_snis

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


def _fleet(db):
    seed_snis(db)
    node = make_node(db)
    make_inbound(db, node)
    make_inbound(db, node, port=8443)
    db.commit()
    return node


# --- the account ---------------------------------------------------------


def test_the_same_account_comes_back_every_time(db):
    """A new one per restart would leak accounts and inbound slots."""
    first = egress.get_or_create(db)
    db.commit()
    second = egress.get_or_create(db)
    db.commit()

    assert first.id == second.id
    assert len(db.scalars(select(User)).all()) == 1
    identities = db.scalars(
        select(AuthIdentity).where(AuthIdentity.provider_uid == egress.EGRESS_UID)
    ).all()
    assert len(identities) == 1


def test_its_access_is_refreshed_on_every_lookup(db):
    """An expired egress takes the bot down and says nothing about why."""
    user = egress.get_or_create(db)
    db.commit()

    user.access_expires_at = None
    db.commit()

    again = egress.get_or_create(db)
    db.commit()
    assert again.access_expires_at is not None
    assert not again.access_is_grace


# --- the endpoints -------------------------------------------------------


def test_connect_hands_the_proxy_a_config(client, db):
    _fleet(db)
    response = client.post("/api/v1/egress/connect")
    assert response.status_code == 200, response.text
    assert response.json()["vless_url"].startswith("vless://")


def test_connect_needs_no_advertisement(client, db):
    """The egress buys nothing and is not metered.

    If it were, the alert path would expire on its own schedule and go
    quiet without anyone touching it.
    """
    _fleet(db)
    assert client.post("/api/v1/egress/connect").status_code == 200
    assert client.post("/api/v1/egress/connect").status_code == 200


def test_report_failure_moves_it_and_says_so(client, db):
    _fleet(db)
    assert client.post("/api/v1/egress/connect").status_code == 200

    response = client.post("/api/v1/egress/report-failure")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["vless_url"].startswith("vless://")
    assert "inbound_declared_dead" in body


def test_with_no_node_it_says_so_rather_than_pretending(client, db):
    response = client.post("/api/v1/egress/connect")
    assert response.status_code == 503, response.text


@pytest.mark.parametrize("path", ["/api/v1/egress/connect", "/api/v1/egress/report-failure"])
def test_the_apk_token_cannot_reach_these(db, monkeypatch, path):
    """The token inside the APK is public; these hand out free access."""
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr("app.services.config_selector.push_node_config", lambda _db, _n: None)
    with TestClient(app, headers={"X-Service-Token": SERVICE}) as apk:
        assert apk.post(path).status_code == 401
    app.dependency_overrides.clear()
