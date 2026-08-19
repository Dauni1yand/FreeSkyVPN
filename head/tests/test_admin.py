"""End-to-end exercise of the admin panel over real HTTP.

Every page is rendered and every mutating action is performed, because the
panel is where a mistake is most expensive: its buttons change live
infrastructure and people's paid service. Template errors in particular only
show up when a page is actually rendered, so smoke-rendering all of them is
the point rather than a formality.

Provisioning is stubbed — it needs a real machine to SSH into — but the
route around it, including the failure path, is exercised for real.
"""

from __future__ import annotations

import uuid
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
from app.db.models.admin import AdminAudit
from app.db.models.node import Node, NodeStatus, SniCandidate
from app.db.models.user import UserStatus
from app.main import app
from app.services import provisioning
from app.services.admin_auth import ensure_admin
from tests.factories import make_inbound, make_node, make_user, seed_snis

ADMIN_USER = "operator"
ADMIN_PASSWORD = "correct-horse-battery"


@pytest.fixture
def admin_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HEAD_SECRET_KEY", "test-secret-key-for-sessions")
    monkeypatch.setenv("SECRETS_KEY", "test-secrets-encryption-key")
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")  # TestClient speaks http
    # Background loops would run against the configured production database.
    monkeypatch.setenv("BACKGROUND_JOBS_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def session_factory(admin_env):
    # StaticPool: TestClient runs the app on another thread, and the default
    # pool would hand that thread its own (empty) in-memory database.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, future=True)


@pytest.fixture
def db(session_factory) -> Session:
    with session_factory() as session:
        yield session


@pytest.fixture
def client(session_factory, db, monkeypatch):
    ensure_admin(db, ADMIN_USER, ADMIN_PASSWORD)
    db.commit()

    # Share the test's session with the app so setup and assertions see the
    # same rows the routes act on.
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr("app.services.config_selector.push_node_config", lambda _db, _node: None)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def auth(client):
    response = client.post(
        "/admin/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, "login should redirect to the dashboard"
    return client


# --- authentication ------------------------------------------------------


def test_anonymous_is_redirected_to_login(client):
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_login_page_renders(client):
    response = client.get("/admin/login")
    assert response.status_code == 200
    assert "Пароль" in response.text


def test_wrong_password_is_rejected(client):
    client.post("/admin/login", data={"username": ADMIN_USER, "password": "nope"})
    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_unknown_user_is_rejected(client):
    client.post("/admin/login", data={"username": "ghost", "password": "whatever"})
    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_logout_ends_the_session(auth):
    auth.get("/admin/logout")
    assert auth.get("/admin", follow_redirects=False).status_code == 303


def test_a_forged_session_cookie_is_not_accepted(client):
    client.cookies.set("freesky_admin", "made-up-token")
    assert client.get("/admin", follow_redirects=False).status_code == 303


# --- pages render --------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/admin",
        "/admin/nodes",
        "/admin/users",
        "/admin/sni",
        "/admin/updates",
        "/admin/events",
        "/admin/audit",
    ],
)
def test_every_page_renders_when_empty(auth, path):
    """An empty install must not 500 — this is the first thing an operator sees."""
    response = auth.get(path)
    assert response.status_code == 200, response.text[:400]


@pytest.mark.parametrize(
    "path",
    [
        "/admin",
        "/admin/nodes",
        "/admin/users",
        "/admin/sni",
        "/admin/updates",
        "/admin/events",
    ],
)
def test_every_page_renders_with_data(auth, db, path):
    seed_snis(db)
    node = make_node(db)
    inbound = make_inbound(db, node)
    user = make_user(db)
    from tests.factories import make_assignment

    make_assignment(db, user, inbound)
    db.commit()

    response = auth.get(path)
    assert response.status_code == 200, response.text[:400]


# --- node management -----------------------------------------------------


def test_adding_a_node_reports_provisioning_failure_instead_of_crashing(auth, monkeypatch):
    def boom(*args, **kwargs):
        raise provisioning.ProvisioningError("ssh timed out")

    monkeypatch.setattr(provisioning, "provision_node", boom)

    response = auth.post(
        "/admin/nodes/add",
        data={
            "host": "203.0.113.5",
            "country": "nl",
            "ssh_user": "root",
            "ssh_password": "hunter2",
            "ssh_port": "22",
            "uplink_mbit": "100",
            "capacity": "200",
            "control_sni": "www.microsoft.com",
        },
    )
    assert response.status_code == 200
    assert "ssh timed out" in response.text


def test_successful_add_records_an_audit_entry(auth, db, monkeypatch):
    monkeypatch.setattr(
        provisioning,
        "provision_node",
        lambda *a, **k: provisioning.ProvisionResult(node_id="x", log=["done"]),
    )

    auth.post(
        "/admin/nodes/add",
        data={
            "host": "203.0.113.6",
            "country": "de",
            "ssh_user": "root",
            "ssh_password": "hunter2",
            "ssh_port": "22",
            "uplink_mbit": "100",
            "capacity": "200",
            "control_sni": "www.microsoft.com",
        },
    )

    entry = db.query(AdminAudit).filter(AdminAudit.action == "node.add").one()
    assert entry.target == "203.0.113.6"
    assert entry.admin_username == ADMIN_USER


def test_changing_node_capacity(auth, db):
    node = make_node(db, capacity=200)
    db.commit()

    auth.post(f"/admin/nodes/{node.id}/capacity", data={"capacity": "50"})

    db.refresh(node)
    assert node.capacity == 50


def test_capacity_cannot_be_set_to_zero(auth, db):
    """Zero would take the node out of rotation silently, which is what
    draining is for — and much less obvious to an operator."""
    node = make_node(db, capacity=200)
    db.commit()

    auth.post(f"/admin/nodes/{node.id}/capacity", data={"capacity": "0"})

    db.refresh(node)
    assert node.capacity == 1


def test_draining_a_node_keeps_it_but_takes_it_out_of_rotation(auth, db):
    node = make_node(db)
    db.commit()

    auth.post(f"/admin/nodes/{node.id}/status", data={"status": "draining"})

    db.refresh(node)
    assert node.status == NodeStatus.draining
    assert db.query(Node).count() == 1


def test_deleting_a_node_warns_about_stranded_users(auth, db):
    from tests.factories import make_assignment

    seed_snis(db)
    node = make_node(db)
    inbound = make_inbound(db, node)
    make_assignment(db, make_user(db), inbound)
    db.commit()

    response = auth.post(f"/admin/nodes/{node.id}/delete")

    assert db.query(Node).count() == 0
    assert "без конфига" in response.text


def test_rotating_password_reports_ssh_failure(auth, db, monkeypatch):
    from app.services.ssh_manager import SshError

    node = make_node(db)
    db.commit()
    monkeypatch.setattr(
        provisioning, "rotate_node_password", lambda *a, **k: (_ for _ in ()).throw(SshError("no route"))
    )

    response = auth.post(f"/admin/nodes/{node.id}/rotate-password")
    assert "no route" in response.text


def test_acting_on_a_missing_node_is_reported_not_crashed(auth):
    response = auth.post(f"/admin/nodes/{uuid.uuid4()}/capacity", data={"capacity": "10"})
    assert response.status_code == 200
    assert "не найдена" in response.text


# --- user management -----------------------------------------------------


def test_granting_access_by_hand(auth, db):
    """An operator can put someone online without an ad — for support, or
    for testing a node."""
    user = make_user(db)
    db.commit()

    auth.post(f"/admin/users/{user.id}/grant", data={"hours": "3"})

    db.expire_all()
    from app.services import access

    state = access.state_of(db.get(type(user), user.id))
    assert state.active
    assert 2 * 3600 < state.seconds_remaining <= 3 * 3600


def test_a_manual_grant_stacks_rather_than_replacing(auth, db):
    user = make_user(db)
    db.commit()
    auth.post(f"/admin/users/{user.id}/grant", data={"hours": "2"})
    auth.post(f"/admin/users/{user.id}/grant", data={"hours": "2"})

    db.expire_all()
    from app.services import access

    assert access.state_of(db.get(type(user), user.id)).seconds_remaining > 3 * 3600


def test_a_manual_grant_records_the_operator(auth, db):
    """It bypasses the ads, so the gap has to be answerable."""
    from app.db.models.logs import AdView

    user = make_user(db)
    db.commit()
    auth.post(f"/admin/users/{user.id}/grant", data={"hours": "1"})

    db.expire_all()
    assert db.query(AdView).one().source == f"manual:{ADMIN_USER}"


def test_granting_zero_hours_is_refused(auth, db):
    user = make_user(db)
    db.commit()

    response = auth.post(
        f"/admin/users/{user.id}/grant", data={"hours": "0"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert "err" in response.headers["set-cookie"]


def test_revoking_access(auth, db):
    from app.services import access

    user = make_user(db)
    access.grant_manual(db, user, 120, by="test")
    db.commit()

    auth.post(f"/admin/users/{user.id}/revoke")

    db.expire_all()
    assert not access.has_access(db.get(type(user), user.id))


def test_banning_and_unbanning(auth, db):
    user = make_user(db)
    db.commit()

    auth.post(f"/admin/users/{user.id}/ban", data={"banned": "1"})
    db.refresh(user)
    assert user.status == UserStatus.banned

    auth.post(f"/admin/users/{user.id}/ban", data={"banned": "0"})
    db.refresh(user)
    assert user.status == UserStatus.active


def test_user_search_by_telegram_id(auth, db):
    from app.db.models.user import AuthIdentity, AuthProvider

    user = make_user(db)
    db.add(AuthIdentity(user_id=user.id, provider=AuthProvider.telegram, provider_uid="998877"))
    make_user(db)
    db.commit()

    response = auth.get("/admin/users?q=998877")
    assert response.status_code == 200
    assert "998877" in response.text


def test_user_search_with_no_matches_renders_empty(auth, db):
    make_user(db)
    db.commit()
    response = auth.get("/admin/users?q=nothing-matches-this")
    assert response.status_code == 200
    assert "Ничего не найдено" in response.text


# --- plans and SNI -------------------------------------------------------


def test_adding_an_sni_domain(auth, db):
    auth.post("/admin/sni/add", data={"domain": "www.vendor-example.com"})
    assert db.query(SniCandidate).filter(SniCandidate.domain == "www.vendor-example.com").one()


def test_duplicate_sni_is_refused(auth, db):
    db.add(SniCandidate(domain="dupe.com"))
    db.commit()

    response = auth.post("/admin/sni/add", data={"domain": "dupe.com"})
    assert "уже в пуле" in response.text
    assert db.query(SniCandidate).count() == 1


def test_sni_refresh_reports_a_broken_source_instead_of_500(auth, monkeypatch):
    monkeypatch.setattr(
        "app.admin.router.refresh_candidates",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network unreachable")),
    )
    response = auth.post("/admin/sni/refresh")
    assert response.status_code == 200
    assert "недоступен" in response.text


def test_sni_toggle(auth, db):
    candidate = SniCandidate(domain="toggle.com")
    db.add(candidate)
    db.commit()

    auth.post(f"/admin/sni/{candidate.id}/toggle")
    db.refresh(candidate)
    assert candidate.active is False


# --- Xray updates --------------------------------------------------------


def _propose(db, node, version="26.3.27", **kwargs):
    from app.db.models.update import NodeUpdate

    row = NodeUpdate(
        node_id=node.id,
        target_version=version,
        version_before=kwargs.pop("version_before", "26.3.20"),
        **kwargs,
    )
    db.add(row)
    db.commit()
    return row


def test_updates_page_renders_pending_proposals_and_history(auth, db):
    """The pending block has the most template logic on the panel — grouping,
    per-version bulk actions, conditional buttons — so it is rendered with
    real rows rather than only in the empty state."""
    from app.db.models.update import NodeUpdateStatus

    nl = make_node(db, country="nl")
    de = make_node(db, country="de")
    _propose(db, nl)
    _propose(db, de)
    _propose(
        db,
        nl,
        version="26.3.10",
        status=NodeUpdateStatus.applied,
        version_after="26.3.10",
        decided_by="admin",
        finished_at=datetime.now(UTC),
    )

    response = auth.get("/admin/updates")

    assert response.status_code == 200, response.text[:400]
    assert "26.3.27" in response.text
    assert "Обновить все (2)" in response.text, "a fleet-wide release should be one action"


def test_approving_an_update_from_the_panel_queues_it(auth, db):
    from app.db.models.update import NodeUpdate, NodeUpdateStatus

    row = _propose(db, make_node(db))

    auth.post(f"/admin/updates/{row.id}/decide", data={"approve": "1"})

    db.expire_all()
    assert db.get(NodeUpdate, row.id).status == NodeUpdateStatus.approved


def test_declining_an_update_from_the_panel(auth, db):
    from app.db.models.update import NodeUpdate, NodeUpdateStatus

    row = _propose(db, make_node(db))

    auth.post(f"/admin/updates/{row.id}/decide", data={"approve": "0"})

    db.expire_all()
    assert db.get(NodeUpdate, row.id).status == NodeUpdateStatus.declined


def test_deciding_an_already_decided_update_says_so(auth, db):
    """The same update can be approved from Telegram a second earlier. The
    panel must report that rather than pretend it did something."""
    row = _propose(db, make_node(db))
    auth.post(f"/admin/updates/{row.id}/decide", data={"approve": "1"})

    response = auth.post(
        f"/admin/updates/{row.id}/decide", data={"approve": "1"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert "%D1%83%D0%B6%D0%B5" in response.headers["set-cookie"]  # "уже"


def test_approve_all_covers_every_node_on_that_version(auth, db):
    from app.db.models.update import NodeUpdate, NodeUpdateStatus

    for country in ("nl", "de", "fi"):
        _propose(db, make_node(db, country=country))

    auth.post("/admin/updates/approve-all", data={"target_version": "26.3.27"})

    db.expire_all()
    approved = db.query(NodeUpdate).filter_by(status=NodeUpdateStatus.approved).count()
    assert approved == 3


def test_manual_check_reports_an_unreachable_release_feed(auth, monkeypatch):
    """GitHub is not reliably reachable from a head in RF. That has to read
    as a message, not as a broken panel."""
    monkeypatch.setattr(
        "app.services.xray_updates.latest_release_version", lambda **_kw: None
    )

    response = auth.post("/admin/updates/check", follow_redirects=False)

    assert response.status_code == 303
    assert "warn" in response.headers["set-cookie"]


def test_updates_page_never_calls_github_while_rendering(auth, monkeypatch):
    """A page a human is waiting on must not spend a network timeout on a
    decoration in its corner."""
    def boom(*_a, **_kw):
        raise AssertionError("the updates page must not make a live release lookup")

    monkeypatch.setattr("app.services.xray_updates.httpx.get", boom)
    assert auth.get("/admin/updates").status_code == 200
