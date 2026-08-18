"""Xray update detection, approval and application.

The GitHub release feed is stubbed throughout rather than called: it is
unreachable from CI, and a test whose verdict depends on someone else's
uptime is not a test. What is exercised here is everything the head does
around that one lookup — which is where all the behaviour lives.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models.node import NodeStatus
from app.db.models.update import NodeUpdate, NodeUpdateStatus
from app.services import xray_updates
from app.services.xray_updates import UpdateOutcome
from tests.factories import make_node


@pytest.fixture(autouse=True)
def clear_release_cache():
    xray_updates._cache.clear()
    yield
    xray_updates._cache.clear()


# --- version parsing -----------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("26.3.27", (26, 3, 27)),
        ("v26.3.27", (26, 3, 27)),
        # What `xray version` actually prints on its first line.
        ("Xray 26.3.27 (Xray, Penetrates Everything.) Custom", (26, 3, 27)),
        ("1.8.24", (1, 8, 24)),
        ("", None),
        (None, None),
        ("no digits here", None),
    ],
)
def test_parse_version_handles_every_form_it_is_handed(raw, expected):
    assert xray_updates.parse_version(raw) == expected


def test_version_comparison_is_numeric_not_lexicographic():
    # The case that matters: Xray's versioning jumped from 1.x to 25.x, so
    # string comparison would call 9.1.0 newer than 26.3.27 and every node
    # would look permanently up to date.
    assert xray_updates.is_newer("26.3.27", "9.1.0")
    assert not xray_updates.is_newer("9.1.0", "26.3.27")
    assert not xray_updates.is_newer("26.3.27", "26.3.27")
    assert xray_updates.is_newer("26.4.0", "26.3.27")


def test_unknown_current_version_is_not_treated_as_outdated():
    # A node whose control channel is down reports nothing. Proposing an
    # update for it would ask an operator to authorise restarting something
    # we cannot even reach.
    assert not xray_updates.is_newer("26.3.27", None)
    assert not xray_updates.is_newer("26.3.27", "")


# --- release lookup ------------------------------------------------------


def test_release_lookup_returns_none_when_github_is_unreachable(monkeypatch):
    def boom(*_a, **_kw):
        raise OSError("network unreachable")

    monkeypatch.setattr(xray_updates.httpx, "get", boom)
    assert xray_updates.latest_release_version() is None


def test_release_lookup_is_cached_between_calls(monkeypatch):
    calls = {"n": 0}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            calls["n"] += 1
            return {"tag_name": "v26.3.27"}

    monkeypatch.setattr(xray_updates.httpx, "get", lambda *_a, **_kw: FakeResponse())

    assert xray_updates.latest_release_version() == "26.3.27"
    assert xray_updates.latest_release_version() == "26.3.27"
    assert calls["n"] == 1


def test_cached_only_lookup_never_touches_the_network(monkeypatch):
    # The admin page uses this. On a head that cannot reach GitHub, a live
    # lookup would cost the full timeout on every page load.
    def boom(*_a, **_kw):
        raise AssertionError("cached_only must not make a request")

    monkeypatch.setattr(xray_updates.httpx, "get", boom)
    assert xray_updates.latest_release_version(cached_only=True) is None


# --- detection -----------------------------------------------------------


def _stub_versions(monkeypatch, latest: str | None, node_version: str | None):
    monkeypatch.setattr(
        xray_updates, "latest_release_version", lambda **_kw: latest
    )
    monkeypatch.setattr(xray_updates, "node_core_version", lambda _db, _node: node_version)


def test_check_raises_one_proposal_per_outdated_node(db, monkeypatch):
    make_node(db, country="nl")
    make_node(db, country="de")
    _stub_versions(monkeypatch, "26.3.27", "26.3.20")

    raised = xray_updates.check_for_updates(db)

    assert len(raised) == 2
    assert all(u.status == NodeUpdateStatus.pending for u in raised)
    assert all(u.target_version == "26.3.27" for u in raised)
    assert all(u.version_before == "26.3.20" for u in raised)


def test_check_raises_nothing_when_nodes_are_current(db, monkeypatch):
    make_node(db)
    _stub_versions(monkeypatch, "26.3.27", "26.3.27")
    assert xray_updates.check_for_updates(db) == []


def test_check_skips_draining_nodes(db, monkeypatch):
    make_node(db, status=NodeStatus.draining)
    _stub_versions(monkeypatch, "26.3.27", "26.3.20")
    assert xray_updates.check_for_updates(db) == []


def test_unreachable_release_feed_is_a_quiet_pass_not_a_failure(db, monkeypatch):
    make_node(db)
    _stub_versions(monkeypatch, None, "26.3.20")
    assert xray_updates.check_for_updates(db) == []


def test_a_second_check_does_not_duplicate_an_open_proposal(db, monkeypatch):
    make_node(db)
    _stub_versions(monkeypatch, "26.3.27", "26.3.20")

    xray_updates.check_for_updates(db)
    assert xray_updates.check_for_updates(db) == []
    assert db.query(NodeUpdate).count() == 1


def test_a_declined_version_is_never_proposed_again(db, monkeypatch):
    make_node(db)
    _stub_versions(monkeypatch, "26.3.27", "26.3.20")
    raised = xray_updates.check_for_updates(db)
    xray_updates.decide(db, [raised[0].id], approve=False, by="admin")

    assert xray_updates.check_for_updates(db) == []


def test_an_applied_but_ineffective_update_does_not_nag(db, monkeypatch):
    """The node's Xray comes from the marzban-node image, which can lag the
    release. Without this the operator would be asked again every 12 hours
    about an update that has already been attempted and cannot land yet."""
    node = make_node(db)
    db.add(
        NodeUpdate(
            node_id=node.id,
            target_version="26.3.27",
            version_before="26.3.20",
            version_after="26.3.20",
            status=NodeUpdateStatus.applied,
            finished_at=datetime.now(UTC),
        )
    )
    db.flush()
    _stub_versions(monkeypatch, "26.3.27", "26.3.20")

    assert xray_updates.check_for_updates(db) == []


def test_an_ineffective_update_is_retried_once_the_image_has_had_time(db, monkeypatch):
    """The suppression above is a cooldown, not a permanent block: the image
    does eventually pick the release up, and a node stuck one version behind
    forever is the failure mode of getting this wrong."""
    node = make_node(db)
    db.add(
        NodeUpdate(
            node_id=node.id,
            target_version="26.3.27",
            version_before="26.3.20",
            version_after="26.3.20",
            status=NodeUpdateStatus.applied,
            finished_at=datetime.now(UTC) - timedelta(hours=48),
        )
    )
    db.flush()
    _stub_versions(monkeypatch, "26.3.27", "26.3.20")

    assert len(xray_updates.check_for_updates(db)) == 1


def test_a_failed_attempt_is_retried_after_the_cooldown(db, monkeypatch):
    node = make_node(db)
    db.add(
        NodeUpdate(
            node_id=node.id,
            target_version="26.3.27",
            status=NodeUpdateStatus.failed,
            finished_at=datetime.now(UTC) - timedelta(hours=48),
        )
    )
    db.flush()
    _stub_versions(monkeypatch, "26.3.27", "26.3.20")

    assert len(xray_updates.check_for_updates(db)) == 1


def test_a_failed_attempt_is_not_retried_immediately(db, monkeypatch):
    node = make_node(db)
    db.add(
        NodeUpdate(
            node_id=node.id,
            target_version="26.3.27",
            status=NodeUpdateStatus.failed,
            finished_at=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    db.flush()
    _stub_versions(monkeypatch, "26.3.27", "26.3.20")

    assert xray_updates.check_for_updates(db) == []


# --- decisions -----------------------------------------------------------


def test_approving_twice_only_counts_once(db, monkeypatch):
    """Two taps on the same Telegram button, or Telegram racing the admin
    panel, must not queue the node's restart twice."""
    make_node(db)
    _stub_versions(monkeypatch, "26.3.27", "26.3.20")
    xray_updates.check_for_updates(db)

    assert xray_updates.decide_version(db, "26.3.27", approve=True, by="telegram:1") == 1
    assert xray_updates.decide_version(db, "26.3.27", approve=True, by="telegram:1") == 0


def test_declining_after_approving_does_not_undo_the_approval(db, monkeypatch):
    make_node(db)
    _stub_versions(monkeypatch, "26.3.27", "26.3.20")
    xray_updates.check_for_updates(db)
    xray_updates.decide_version(db, "26.3.27", approve=True, by="admin")

    assert xray_updates.decide_version(db, "26.3.27", approve=False, by="admin") == 0
    assert db.query(NodeUpdate).one().status == NodeUpdateStatus.approved


def test_decide_by_version_covers_every_node_waiting_on_it(db, monkeypatch):
    make_node(db, country="nl")
    make_node(db, country="de")
    make_node(db, country="fi")
    _stub_versions(monkeypatch, "26.3.27", "26.3.20")
    xray_updates.check_for_updates(db)

    assert xray_updates.decide_version(db, "26.3.27", approve=True, by="telegram:1") == 3


def test_the_deciding_operator_is_recorded(db, monkeypatch):
    make_node(db)
    _stub_versions(monkeypatch, "26.3.27", "26.3.20")
    xray_updates.check_for_updates(db)
    xray_updates.decide_version(db, "26.3.27", approve=True, by="telegram:4242")

    row = db.query(NodeUpdate).one()
    assert row.decided_by == "telegram:4242"
    assert row.decided_at is not None


# --- application ---------------------------------------------------------


def _pending_row(db, monkeypatch, *, target="26.3.27", current="26.3.20") -> NodeUpdate:
    make_node(db)
    _stub_versions(monkeypatch, target, current)
    (row,) = xray_updates.check_for_updates(db)
    xray_updates.decide_version(db, target, approve=True, by="admin")
    return row


def test_a_successful_apply_records_both_versions(db, monkeypatch):
    _pending_row(db, monkeypatch)
    monkeypatch.setattr(
        xray_updates,
        "_run_update_on_node",
        lambda _node: UpdateOutcome(ok=True, before="26.3.20", after="26.3.27", error=None),
    )

    (applied,) = xray_updates.apply_approved(db)

    assert applied.status == NodeUpdateStatus.applied
    assert applied.version_before == "26.3.20"
    assert applied.version_after == "26.3.27"
    assert applied.error is None
    assert applied.finished_at is not None


def test_an_image_that_lags_the_release_is_a_note_not_a_failure(db, monkeypatch):
    """The update ran, the node came back, it is just still behind upstream.
    Calling that a failure would send the operator hunting a broken node."""
    _pending_row(db, monkeypatch)
    monkeypatch.setattr(
        xray_updates,
        "_run_update_on_node",
        lambda _node: UpdateOutcome(ok=True, before="26.3.20", after="26.3.25", error=None),
    )

    (applied,) = xray_updates.apply_approved(db)

    assert applied.status == NodeUpdateStatus.applied
    assert applied.version_after == "26.3.25"
    assert "26.3.27" in applied.error


def test_an_ssh_failure_lands_on_the_row_rather_than_raising(db, monkeypatch):
    """apply_approved runs from a background loop; an escaping exception
    would abandon the row in `applying` and stall every update behind it."""
    _pending_row(db, monkeypatch)

    def boom(_node):
        raise xray_updates.SshError("connection refused")

    monkeypatch.setattr(xray_updates, "_run_update_on_node", boom)

    (failed,) = xray_updates.apply_approved(db)

    assert failed.status == NodeUpdateStatus.failed
    assert "connection refused" in failed.error
    assert failed.finished_at is not None


def test_a_script_that_reports_failure_is_recorded_as_failed(db, monkeypatch):
    _pending_row(db, monkeypatch)
    monkeypatch.setattr(
        xray_updates,
        "_run_update_on_node",
        lambda _node: UpdateOutcome(
            ok=False, before="26.3.20", after=None, error="container did not come back"
        ),
    )

    (failed,) = xray_updates.apply_approved(db)

    assert failed.status == NodeUpdateStatus.failed
    assert failed.error == "container did not come back"


def test_only_approved_rows_are_applied(db, monkeypatch):
    make_node(db)
    _stub_versions(monkeypatch, "26.3.27", "26.3.20")
    xray_updates.check_for_updates(db)  # left pending, nobody approved it

    monkeypatch.setattr(
        xray_updates,
        "_run_update_on_node",
        lambda _node: pytest.fail("a pending update must never be applied"),
    )
    assert xray_updates.apply_approved(db) == []


def test_updates_are_applied_one_node_at_a_time(db, monkeypatch):
    """Each apply restarts a node's Xray. Doing the fleet at once would drop
    every user on every node simultaneously."""
    for country in ("nl", "de", "fi"):
        make_node(db, country=country)
    _stub_versions(monkeypatch, "26.3.27", "26.3.20")
    xray_updates.check_for_updates(db)
    xray_updates.decide_version(db, "26.3.27", approve=True, by="admin")

    monkeypatch.setattr(
        xray_updates,
        "_run_update_on_node",
        lambda _node: UpdateOutcome(ok=True, before="26.3.20", after="26.3.27", error=None),
    )

    assert len(xray_updates.apply_approved(db)) == 1
    assert db.query(NodeUpdate).filter_by(status=NodeUpdateStatus.approved).count() == 2


# --- reading the node script's output ------------------------------------
#
# The boundary between provisioning/update_node.sh and this module is a
# single JSON line on stdout. Every case below is something that line can
# realistically be.


class _FakeSsh:
    def __init__(self, stdout: str, stderr: str = "", exit_status: int = 0):
        self.result = type(
            "Result", (), {"stdout": stdout, "stderr": stderr, "exit_status": exit_status}
        )()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _stub_ssh(monkeypatch, stdout: str, stderr: str = "", exit_status: int = 0):
    session = _FakeSsh(stdout, stderr, exit_status)
    monkeypatch.setattr(xray_updates.ssh_manager, "connect", lambda *_a, **_kw: session)
    monkeypatch.setattr(
        xray_updates.ssh_manager, "run", lambda *_a, **_kw: session.result
    )
    monkeypatch.setattr(xray_updates, "_update_script_source", lambda: "#!/bin/bash\n")


def test_the_json_line_is_read_off_the_end_of_the_output(db, monkeypatch):
    # The script logs to stderr, but `docker pull` writes progress to stdout
    # too, so the payload is the last line rather than the only one.
    node = make_node(db)
    _stub_ssh(
        monkeypatch,
        "latest: Pulling from gozargah/marzban-node\n"
        "Digest: sha256:abc\n"
        '{"ok": true, "before": "26.3.20", "after": "26.3.27", "error": ""}\n',
    )

    outcome = xray_updates._run_update_on_node(node)

    assert outcome.ok
    assert outcome.before == "26.3.20"
    assert outcome.after == "26.3.27"
    assert outcome.error is None


def test_an_empty_error_string_becomes_none(db, monkeypatch):
    # printf writes "" rather than omitting the field, and "" is not an error.
    node = make_node(db)
    _stub_ssh(monkeypatch, '{"ok": true, "before": "1.0.0", "after": "2.0.0", "error": ""}')

    assert xray_updates._run_update_on_node(node).error is None


def test_silence_from_the_node_is_an_error_not_a_success(db, monkeypatch):
    node = make_node(db)
    _stub_ssh(monkeypatch, "", stderr="bash: docker: command not found", exit_status=127)

    with pytest.raises(xray_updates.XrayUpdateError, match="no output"):
        xray_updates._run_update_on_node(node)


def test_unparseable_output_is_an_error_not_a_success(db, monkeypatch):
    node = make_node(db)
    _stub_ssh(monkeypatch, "Killed\n")

    with pytest.raises(xray_updates.XrayUpdateError, match="could not parse"):
        xray_updates._run_update_on_node(node)
