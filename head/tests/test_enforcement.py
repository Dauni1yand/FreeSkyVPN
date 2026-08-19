"""Cutting off a user whose time has run out.

Worth stating plainly what this covers, because it was the hole in the
model: everything else only gated *issuing* a config. `/me/connect` refuses
without access and the placement sweep moves lapsed users to the slower
class, but neither touches a tunnel that is already up. A node runs the
config it was last given, and that config lists client UUIDs — so before
this, one watched ad bought an unlimited VPN.

These tests are therefore about the node's config, not about the database.
A row saying "released" while the UUID is still on the node is not a
disconnection.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models.node import Assignment
from app.db.models.user import UserStatus
from app.node_manager.config_render import render_node_config
from app.services import access, enforcement
from app.services.config_selector import assign_config
from tests.factories import make_node, make_user, seed_snis


@pytest.fixture
def node_pushes(monkeypatch):
    """Records the config each push would actually send to a node."""
    pushed = []

    def fake_push(db, node):
        from sqlalchemy import select

        from app.db.models.node import Inbound

        inbounds = db.scalars(select(Inbound).where(Inbound.node_id == node.id)).all()
        pushed.append((node, json.loads(render_node_config(list(inbounds)))))

    monkeypatch.setattr("app.services.enforcement.push_node_config", fake_push)
    monkeypatch.setattr("app.services.config_selector.push_node_config", lambda _db, _n: None)
    return pushed


def _uuids_in(config: dict) -> set[str]:
    return {
        client["id"]
        for inbound in config["inbounds"]
        for client in inbound.get("settings", {}).get("clients", [])
    }


def _connected_user(db, *, minutes: int = 60):
    seed_snis(db)
    make_node(db)
    user = make_user(db)
    nonce = access.issue_nonce(db, user, "hour")
    access.redeem_nonce(db, user, nonce.nonce)
    if minutes != 60:
        user.access_expires_at = datetime.now(UTC) + timedelta(minutes=minutes)
    assign_config(db, user)
    db.flush()
    return user


def _expire(db, user):
    user.access_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()


# --- the hole this closes ------------------------------------------------


def test_an_expired_user_is_removed_from_the_node_config(db, node_pushes):
    """The actual disconnection. Anything less leaves the tunnel running."""
    user = _connected_user(db)
    xray_uuid = db.query(Assignment).one().xray_uuid
    _expire(db, user)

    enforcement.sweep_expired(db)

    assert node_pushes, "the node must be told, or nothing happened"
    _node, config = node_pushes[-1]
    assert xray_uuid not in _uuids_in(config)


def test_a_user_with_time_left_is_untouched(db, node_pushes):
    _connected_user(db)
    xray_uuid = db.query(Assignment).one().xray_uuid

    outcome = enforcement.sweep_expired(db)

    assert outcome.disconnected == 0
    assert node_pushes == [], "an idle sweep must not restart anyone's node"
    assert db.query(Assignment).one().released_at is None
    assert xray_uuid  # still theirs


def test_the_assignment_is_released(db, node_pushes):
    user = _connected_user(db)
    _expire(db, user)

    enforcement.sweep_expired(db)

    assert db.query(Assignment).one().released_at is not None


def test_a_banned_user_is_disconnected_too(db, node_pushes):
    """A ban that leaves the tunnel up is not a ban."""
    user = _connected_user(db)
    user.status = UserStatus.banned
    db.flush()

    outcome = enforcement.sweep_expired(db)

    assert outcome.disconnected == 1


def test_the_control_channel_survives_the_sweep(db, node_pushes):
    """Removing it would cut the head's own fallback path to the node."""
    from tests.factories import make_inbound

    user = _connected_user(db)
    node = db.query(Assignment).one().inbound.node
    make_inbound(db, node, port=8443, is_control_channel=True, control_client_uuid="control-1")
    _expire(db, user)

    enforcement.sweep_expired(db)

    _node, config = node_pushes[-1]
    assert "control-1" in _uuids_in(config)


# --- doing it once per node ---------------------------------------------


def test_one_push_per_node_not_per_user(db, node_pushes):
    """Every push restarts that node's Xray. Pushing per user would take a
    busy node down repeatedly for one logical change."""
    seed_snis(db)
    make_node(db)
    users = []
    for _ in range(3):
        user = make_user(db)
        nonce = access.issue_nonce(db, user, "hour")
        access.redeem_nonce(db, user, nonce.nonce)
        assign_config(db, user)
        users.append(user)
    db.flush()
    for user in users:
        _expire(db, user)

    outcome = enforcement.sweep_expired(db)

    assert outcome.disconnected == 3
    assert outcome.nodes_pushed == 1
    assert len(node_pushes) == 1


# --- failure --------------------------------------------------------------


def test_an_unreachable_node_keeps_its_users_assigned(db, monkeypatch):
    """A database saying "disconnected" while the tunnel is still up is a
    database that will never retry. The truth is the node's config."""
    monkeypatch.setattr("app.services.config_selector.push_node_config", lambda _db, _n: None)
    user = _connected_user(db)
    _expire(db, user)

    def boom(_db, _node):
        raise RuntimeError("node unreachable")

    monkeypatch.setattr("app.services.enforcement.push_node_config", boom)

    outcome = enforcement.sweep_expired(db)

    assert outcome.disconnected == 0
    assert outcome.failed_nodes == 1
    assert db.query(Assignment).one().released_at is None, "must be retried next sweep"


def test_the_sweep_retries_after_a_failure(db, monkeypatch):
    monkeypatch.setattr("app.services.config_selector.push_node_config", lambda _db, _n: None)
    user = _connected_user(db)
    _expire(db, user)

    failing = {"yes": True}

    def sometimes(_db, _node):
        if failing["yes"]:
            raise RuntimeError("node unreachable")

    monkeypatch.setattr("app.services.enforcement.push_node_config", sometimes)
    enforcement.sweep_expired(db)

    failing["yes"] = False
    outcome = enforcement.sweep_expired(db)

    assert outcome.disconnected == 1


def test_who_needs_disconnecting_is_answerable_on_its_own(db, node_pushes):
    user = _connected_user(db)
    assert enforcement.users_to_disconnect(db) == []

    _expire(db, user)
    assert [u.id for u, _a in enforcement.users_to_disconnect(db)] == [user.id]
