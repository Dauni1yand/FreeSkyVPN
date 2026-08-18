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
from datetime import UTC, datetime, timedelta

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
from app.db.models.plan import Plan, Subscription, SubscriptionType
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
    "path", ["/admin", "/admin/nodes", "/admin/users", "/admin/plans", "/admin/sni", "/admin/events", "/admin/audit"]
)
def test_every_page_renders_when_empty(auth, path):
    """An empty install must not 500 — this is the first thing an operator sees."""
    response = auth.get(path)
    assert response.status_code == 200, response.text[:400]


@pytest.mark.parametrize(
    "path", ["/admin", "/admin/nodes", "/admin/users", "/admin/plans", "/admin/sni", "/admin/events"]
)
def test_every_page_renders_with_data(auth, db, path):
    seed_snis(db)
    node = make_node(db)
    inbound = make_inbound(db, node)
    user = make_user(db)
    from tests.factories import make_assignment

    make_assignment(db, user, inbound)
    db.add(Plan(code="m", name="Месяц", duration_days=30, max_devices=2, price=199))
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


def test_granting_a_subscription(auth, db):
    user = make_user(db)
    db.commit()

    auth.post(f"/admin/users/{user.id}/grant", data={"days": "30"})

    sub = db.query(Subscription).filter(Subscription.user_id == user.id).one()
    assert sub.type == SubscriptionType.paid


def test_granting_twice_extends_rather_than_duplicates(auth, db):
    user = make_user(db)
    db.commit()

    auth.post(f"/admin/users/{user.id}/grant", data={"days": "30"})
    first = db.query(Subscription).one().expires_at
    auth.post(f"/admin/users/{user.id}/grant", data={"days": "30"})

    subs = db.query(Subscription).all()
    assert len(subs) == 1, "a second grant must not stack a second subscription row"
    assert subs[0].expires_at > first


def test_revoking_a_subscription(auth, db):
    user = make_user(db)
    db.add(
        Subscription(
            user_id=user.id,
            type=SubscriptionType.paid,
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
    )
    db.commit()

    auth.post(f"/admin/users/{user.id}/revoke")

    from app.services.subscriptions import status_for

    assert status_for(db, user).active is False


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


def test_adding_a_plan(auth, db):
    auth.post(
        "/admin/plans/add",
        data={"code": "half", "name": "Полгода", "duration_days": "180", "max_devices": "3", "price": "899"},
    )
    assert db.query(Plan).filter(Plan.code == "half").one().duration_days == 180


def test_duplicate_plan_code_is_refused(auth, db):
    db.add(Plan(code="dupe", name="x", duration_days=30, max_devices=1, price=1))
    db.commit()

    response = auth.post(
        "/admin/plans/add",
        data={"code": "dupe", "name": "y", "duration_days": "30", "max_devices": "1", "price": "1"},
    )
    assert "уже есть" in response.text
    assert db.query(Plan).count() == 1


def test_hiding_a_plan_keeps_it_for_existing_subscribers(auth, db):
    plan = Plan(code="m", name="Месяц", duration_days=30, max_devices=1, price=199)
    db.add(plan)
    db.commit()

    auth.post(f"/admin/plans/{plan.id}/toggle")

    db.refresh(plan)
    assert plan.active is False
    assert db.query(Plan).count() == 1, "hidden, not deleted"


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
