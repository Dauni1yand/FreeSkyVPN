import pytest

from app.config import get_settings
from app.db.models.logs import FailReport
from app.db.models.node import Assignment, InboundState, SniCandidate
from app.db.models.outbox import ConfigPush, PushReason
from app.services.fail_handler import (
    NoActiveConfigError,
    ReportTooSoonError,
    report_failure,
)
from tests.factories import (
    make_assignment,
    make_inbound,
    make_node,
    make_user,
    seed_snis,
)


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_single_report_moves_only_the_reporter(db, pushes):
    seed_snis(db)
    node = make_node(db)
    broken = make_inbound(db, node, port=443)
    make_inbound(db, node, port=8443)  # somewhere to land

    reporter = make_user(db)
    bystander = make_user(db)
    make_assignment(db, reporter, broken)
    bystander_assignment = make_assignment(db, bystander, broken)

    outcome = report_failure(db, reporter)

    assert outcome.inbound_declared_dead is False
    assert outcome.users_migrated == 1
    assert outcome.config.inbound_id != str(broken.id)

    db.refresh(broken)
    assert broken.state == InboundState.suspect or broken.fail_count == 1
    db.refresh(bystander_assignment)
    assert bystander_assignment.released_at is None, "a lone report must not disturb anyone else"
    assert db.query(ConfigPush).count() == 0


def test_threshold_declares_inbound_dead_and_migrates_everyone(db, pushes, monkeypatch):
    monkeypatch.setenv("INBOUND_FAIL_THRESHOLD", "3")
    monkeypatch.setenv("FAIL_REPORT_COOLDOWN_SECONDS", "0")
    get_settings.cache_clear()

    seed_snis(db)
    node = make_node(db)
    other_node = make_node(db, country="de", host="203.0.113.99")
    broken = make_inbound(db, node, port=443)
    make_inbound(db, other_node, port=443)

    reporters = [make_user(db) for _ in range(3)]
    bystanders = [make_user(db) for _ in range(2)]
    for user in reporters + bystanders:
        make_assignment(db, user, broken)

    # first two reports are below the threshold and move only their reporter
    report_failure(db, reporters[0])
    report_failure(db, reporters[1])
    db.refresh(broken)
    assert broken.state != InboundState.dead

    outcome = report_failure(db, reporters[2])

    assert outcome.inbound_declared_dead is True
    db.refresh(broken)
    assert broken.state == InboundState.dead

    # every bystander is moved off without having tapped anything
    for bystander in bystanders:
        active = (
            db.query(Assignment)
            .filter(Assignment.user_id == bystander.id, Assignment.released_at.is_(None))
            .one()
        )
        assert active.inbound_id != broken.id

    # and each of them is queued for delivery
    pushed_users = {p.user_id for p in db.query(ConfigPush).all()}
    assert pushed_users == {b.id for b in bystanders}
    assert all(p.reason == PushReason.inbound_blocked for p in db.query(ConfigPush).all())


def test_reporter_gets_config_in_response_not_via_outbox(db, pushes, monkeypatch):
    monkeypatch.setenv("INBOUND_FAIL_THRESHOLD", "1")
    monkeypatch.setenv("FAIL_REPORT_COOLDOWN_SECONDS", "0")
    get_settings.cache_clear()

    seed_snis(db)
    node = make_node(db)
    other = make_node(db, country="de", host="203.0.113.99")
    broken = make_inbound(db, node)
    make_inbound(db, other)

    reporter = make_user(db)
    make_assignment(db, reporter, broken)

    outcome = report_failure(db, reporter)

    assert outcome.config.vless_url.startswith("vless://")
    assert db.query(ConfigPush).filter(ConfigPush.user_id == reporter.id).count() == 0


def test_burned_node_migrates_users_to_a_different_node(db, pushes, monkeypatch):
    monkeypatch.setenv("INBOUND_FAIL_THRESHOLD", "1")
    monkeypatch.setenv("FAIL_REPORT_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("NODE_DEAD_INBOUND_THRESHOLD", "1")
    get_settings.cache_clear()

    seed_snis(db)
    burned = make_node(db, country="nl", host="203.0.113.1")
    healthy = make_node(db, country="de", host="203.0.113.2")
    broken = make_inbound(db, burned)
    make_inbound(db, healthy)

    reporter = make_user(db)
    bystander = make_user(db)
    make_assignment(db, reporter, broken)
    make_assignment(db, bystander, broken)

    outcome = report_failure(db, reporter)

    assert outcome.node_declared_burned is True
    assert outcome.config.node_country == "de", "a new port cannot rescue a blocked IP"

    bystander_active = (
        db.query(Assignment)
        .filter(Assignment.user_id == bystander.id, Assignment.released_at.is_(None))
        .one()
    )
    assert bystander_active.inbound.node_id == healthy.id
    assert db.query(ConfigPush).one().reason == PushReason.node_burned


def test_cooldown_rejects_rapid_repeat_taps(db, pushes):
    seed_snis(db)
    node = make_node(db)
    make_inbound(db, node, port=443)
    make_inbound(db, node, port=8443)
    user = make_user(db)
    make_assignment(db, user, node.inbounds[0])

    report_failure(db, user)

    with pytest.raises(ReportTooSoonError) as excinfo:
        report_failure(db, user)
    assert excinfo.value.retry_after_seconds > 0


def test_report_without_a_config_is_rejected(db, pushes):
    seed_snis(db)
    user = make_user(db)
    with pytest.raises(NoActiveConfigError):
        report_failure(db, user)


def test_dead_inbound_burns_its_sni(db, pushes, monkeypatch):
    monkeypatch.setenv("INBOUND_FAIL_THRESHOLD", "1")
    monkeypatch.setenv("FAIL_REPORT_COOLDOWN_SECONDS", "0")
    get_settings.cache_clear()

    seed_snis(db, ["www.samsung.com", "www.nvidia.com"])
    node = make_node(db)
    other = make_node(db, country="de", host="203.0.113.99")
    broken = make_inbound(db, node, sni="www.samsung.com")
    make_inbound(db, other, sni="www.nvidia.com")

    user = make_user(db)
    make_assignment(db, user, broken)

    report_failure(db, user)

    burned = db.query(SniCandidate).filter(SniCandidate.domain == "www.samsung.com").one()
    assert burned.burn_count == 1
    assert burned.last_burned_at is not None
    assert burned.active is True, "a burned SNI is deprioritised, not retired"


def test_every_report_is_recorded_for_audit(db, pushes):
    seed_snis(db)
    node = make_node(db)
    make_inbound(db, node, port=443)
    make_inbound(db, node, port=8443)
    user = make_user(db)
    make_assignment(db, user, node.inbounds[0])

    report_failure(db, user)

    assert db.query(FailReport).count() == 1
