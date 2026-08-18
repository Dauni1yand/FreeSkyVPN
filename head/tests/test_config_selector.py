import pytest

from app.db.models.node import (
    Assignment,
    InboundState,
    NodeChannelState,
    NodeStatus,
)
from app.node_manager.exceptions import NodeUnreachableError
from app.services.config_selector import NoCapacityError, assign_config, eligible_nodes
from tests.factories import (
    make_assignment,
    make_inbound,
    make_node,
    make_user,
    seed_snis,
)


def test_assign_reuses_an_existing_live_inbound(db, pushes):
    seed_snis(db)
    node = make_node(db)
    inbound = make_inbound(db, node)
    user = make_user(db)

    config = assign_config(db, user)

    assert config.inbound_id == str(inbound.id)
    assert config.node_country == "nl"
    assert config.vless_url.startswith("vless://")
    assert pushes == [node], "the node must actually be told about the new client"


def test_assign_creates_an_inbound_when_none_is_live(db, pushes):
    seed_snis(db)
    node = make_node(db)
    make_inbound(db, node, state=InboundState.dead)
    user = make_user(db)

    config = assign_config(db, user)

    created = db.get(Assignment, db.query(Assignment).one().id)
    assert str(created.inbound_id) == config.inbound_id
    assert created.inbound.state == InboundState.active
    # 443 belonged to the dead inbound and is deliberately not recycled: if the
    # port was the blocked part, reusing it would reproduce the block.
    assert created.inbound.port == 8443


def test_isolated_nodes_are_skipped_for_new_assignments(db, pushes):
    seed_snis(db)
    isolated = make_node(db, country="de", channel_state=NodeChannelState.isolated)
    make_inbound(db, isolated)
    user = make_user(db)

    with pytest.raises(NoCapacityError):
        assign_config(db, user)

    assert eligible_nodes(db) == []


def test_control_channel_inbound_is_never_handed_to_a_user(db, pushes):
    seed_snis(db)
    node = make_node(db)
    make_inbound(db, node, port=8443, is_control_channel=True, control_client_uuid="fixed-uuid")
    user = make_user(db)

    config = assign_config(db, user)

    assigned = db.query(Assignment).one()
    assert not assigned.inbound.is_control_channel
    assert config.inbound_id != str(node.inbounds[0].id) or not node.inbounds[0].is_control_channel


def test_reassigning_releases_the_previous_assignment(db, pushes):
    seed_snis(db)
    node = make_node(db)
    inbound = make_inbound(db, node)
    user = make_user(db)
    old = make_assignment(db, user, inbound)

    assign_config(db, user)

    db.refresh(old)
    assert old.released_at is not None
    active = db.query(Assignment).filter(Assignment.released_at.is_(None)).all()
    assert len(active) == 1


def _two_nodes_bad_first(db):
    """A broken node and a working one, with the broken one guaranteed to be tried first.

    Selection orders by load, so without a load difference the order would be
    arbitrary and these tests could pass without ever reaching the broken node.
    """
    seed_snis(db)
    bad = make_node(db, country="de", host="203.0.113.1")
    good = make_node(db, country="nl", host="203.0.113.2")
    make_inbound(db, bad)
    good_inbound = make_inbound(db, good)
    make_assignment(db, make_user(db), good_inbound)  # gives `good` load 1, so `bad` sorts first
    return bad, good


def test_unreachable_node_falls_through_to_the_next_one(db, monkeypatch):
    bad, good = _two_nodes_bad_first(db)
    user = make_user(db)
    attempted = []

    def flaky_push(db_, node):
        attempted.append(node.id)
        if node.id == bad.id:
            raise NodeUnreachableError("blocked")

    monkeypatch.setattr("app.services.config_selector.push_node_config", flaky_push)

    config = assign_config(db, user)

    assert attempted == [bad.id, good.id], "the broken node must actually have been tried"
    assert config.node_country == "nl"
    # the failed attempt must leave nothing behind for this user
    assert db.query(Assignment).filter(
        Assignment.user_id == user.id, Assignment.released_at.is_(None)
    ).count() == 1


def test_failed_attempt_does_not_discard_channel_bookkeeping(db, monkeypatch):
    """The failure counters that drive direct -> tunnel -> isolated must survive.

    Rolling the transaction back on a failed node would reset them on every
    attempt, so a blocked node would never escalate to the Reality tunnel.
    """
    bad, _good = _two_nodes_bad_first(db)
    user = make_user(db)

    def flaky_push(db_, node):
        if node.id == bad.id:
            node.consecutive_primary_fails += 1  # what call_node records internally
            raise NodeUnreachableError("blocked")

    monkeypatch.setattr("app.services.config_selector.push_node_config", flaky_push)

    assign_config(db, user)

    db.refresh(bad)
    assert bad.consecutive_primary_fails == 1


def test_draining_nodes_are_not_eligible(db):
    make_node(db, status=NodeStatus.draining)
    assert eligible_nodes(db) == []
