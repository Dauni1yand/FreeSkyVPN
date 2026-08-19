"""The head/bot contract for Xray updates, over real HTTP.

What matters here is the shape the bot depends on — grouping by version,
the two independent delivery stamps, and acknowledgements that survive
being sent twice — because a mismatch here is invisible until an operator
is either never asked about an update or asked about the same one forever.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.config import get_settings
from app.db import models  # noqa: F401 - registers tables
from app.db.base import Base
from app.db.models.update import NodeUpdate, NodeUpdateStatus
from app.main import app
from tests.factories import make_node

ADMIN = "test-admin-token"


@pytest.fixture
def session_factory(monkeypatch):
    monkeypatch.setenv("HEAD_SECRET_KEY", "irrelevant-here")
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
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app, headers={"X-Admin-Token": ADMIN}) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _propose(db, node, version="26.3.27", **kwargs) -> NodeUpdate:
    row = NodeUpdate(
        node_id=node.id,
        target_version=version,
        version_before=kwargs.pop("version_before", "26.3.20"),
        **kwargs,
    )
    db.add(row)
    db.commit()
    return row


# --- notifications -------------------------------------------------------


def test_proposals_are_grouped_by_version(client, db):
    """One release across the fleet should be one question, not one per node."""
    for country in ("nl", "de", "fi"):
        _propose(db, make_node(db, country=country))
    _propose(db, make_node(db, country="us"), version="26.4.0")

    groups = client.get("/api/v1/xray-updates/notifications").json()

    by_version = {g["target_version"]: g for g in groups}
    assert len(by_version["26.3.27"]["nodes"]) == 3
    assert len(by_version["26.4.0"]["nodes"]) == 1


def test_a_group_carries_what_the_message_needs_to_say(client, db):
    node = make_node(db, country="nl", host="203.0.113.77")
    _propose(db, node)

    (group,) = client.get("/api/v1/xray-updates/notifications").json()

    (info,) = group["nodes"]
    assert info["host"] == "203.0.113.77"
    assert info["country"] == "nl"
    assert info["version_before"] == "26.3.20"


def test_an_announced_proposal_is_not_announced_again(client, db):
    row = _propose(db, make_node(db))

    client.post("/api/v1/xray-updates/notifications/ack", json={"update_ids": [str(row.id)]})

    assert client.get("/api/v1/xray-updates/notifications").json() == []


def test_acking_twice_is_not_a_second_delivery(client, db):
    """The bot acks after Telegram accepted the message; a retried ack must
    not look like the operator was asked twice."""
    row = _propose(db, make_node(db))
    payload = {"update_ids": [str(row.id)]}

    first = client.post("/api/v1/xray-updates/notifications/ack", json=payload).json()
    second = client.post("/api/v1/xray-updates/notifications/ack", json=payload).json()

    assert first["acked"] == 1
    assert second["acked"] == 0


def test_a_decided_proposal_stops_being_offered(client, db):
    _propose(db, make_node(db), status=NodeUpdateStatus.approved)
    assert client.get("/api/v1/xray-updates/notifications").json() == []


# --- decisions -----------------------------------------------------------


def test_deciding_by_version_covers_the_whole_group(client, db):
    for country in ("nl", "de"):
        _propose(db, make_node(db, country=country))

    response = client.post(
        "/api/v1/xray-updates/decide",
        json={"target_version": "26.3.27", "approve": True, "by": "telegram:1"},
    )

    assert response.json()["changed"] == 2
    db.expire_all()
    assert db.query(NodeUpdate).filter_by(status=NodeUpdateStatus.approved).count() == 2


def test_deciding_by_id_touches_only_that_row(client, db):
    keep = _propose(db, make_node(db, country="nl"))
    _propose(db, make_node(db, country="de"))

    response = client.post(
        "/api/v1/xray-updates/decide",
        json={"update_ids": [str(keep.id)], "approve": False, "by": "operator"},
    )

    assert response.json()["changed"] == 1
    db.expire_all()
    assert db.get(NodeUpdate, keep.id).status == NodeUpdateStatus.declined


def test_deciding_without_a_target_is_rejected(client):
    response = client.post("/api/v1/xray-updates/decide", json={"approve": True})
    assert response.status_code == 422


def test_a_second_tap_changes_nothing(client, db):
    _propose(db, make_node(db))
    body = {"target_version": "26.3.27", "approve": True, "by": "telegram:1"}

    assert client.post("/api/v1/xray-updates/decide", json=body).json()["changed"] == 1
    assert client.post("/api/v1/xray-updates/decide", json=body).json()["changed"] == 0


# --- results -------------------------------------------------------------


def test_finished_updates_are_offered_for_reporting(client, db):
    _propose(
        db,
        make_node(db),
        status=NodeUpdateStatus.applied,
        version_after="26.3.27",
        notified_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )

    (result,) = client.get("/api/v1/xray-updates/results").json()

    assert result["status"] == "applied"
    assert result["version_after"] == "26.3.27"


def test_an_update_nobody_was_asked_about_is_not_reported(client, db):
    """Approved from the admin panel: the operator is already watching that
    page, and a Telegram message about it would be noise."""
    _propose(
        db,
        make_node(db),
        status=NodeUpdateStatus.applied,
        notified_at=None,
        finished_at=datetime.now(UTC),
    )

    assert client.get("/api/v1/xray-updates/results").json() == []


def test_an_unfinished_update_is_not_reported(client, db):
    _propose(
        db, make_node(db), status=NodeUpdateStatus.applying, notified_at=datetime.now(UTC)
    )
    assert client.get("/api/v1/xray-updates/results").json() == []


def test_a_reported_result_is_not_reported_again(client, db):
    row = _propose(
        db,
        make_node(db),
        status=NodeUpdateStatus.failed,
        notified_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        error="ssh timed out",
    )

    client.post("/api/v1/xray-updates/results/ack", json={"update_ids": [str(row.id)]})

    assert client.get("/api/v1/xray-updates/results").json() == []


def test_the_two_delivery_stamps_are_independent(client, db):
    """Asking and answering are separate messages that can each be lost."""
    row = _propose(db, make_node(db))

    client.post("/api/v1/xray-updates/notifications/ack", json={"update_ids": [str(row.id)]})

    db.expire_all()
    refreshed = db.get(NodeUpdate, row.id)
    assert refreshed.notified_at is not None
    assert refreshed.reported_at is None


# --- auth ----------------------------------------------------------------


def test_the_endpoints_require_the_admin_token(db):
    """Not the service token: that one ships in the APK, so guarding a
    fleet-wide restart with it would guard nothing."""
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as anon:
        assert anon.get("/api/v1/xray-updates/notifications").status_code == 401
        assert (
            anon.post(
                "/api/v1/xray-updates/decide",
                json={"target_version": "26.3.27", "approve": True},
            ).status_code
            == 401
        )
    app.dependency_overrides.clear()
