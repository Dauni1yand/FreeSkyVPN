"""The free/paid split is *where a user is placed*, so these test placement.

Xray has no per-user speed limit (measured), so a user's tier is expressed
entirely by which node they sit on: free nodes are shaped at provisioning
time, paid nodes are not.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models.node import Assignment, Inbound, Node, NodeTier
from app.db.models.outbox import ConfigPush, PushReason
from app.db.models.plan import Plan, Subscription, SubscriptionType
from app.services.config_selector import NoCapacityError, assign_config
from app.services.subscriptions import start_trial
from app.services.tiering import (
    current_tier,
    reconcile_placement,
    required_tier,
    users_on_wrong_tier,
)
from tests.factories import make_inbound, make_node, make_user, seed_snis


def _subscribe(db, user, days=30):
    plan = Plan(code=f"p{user.id.hex[:6]}", name="test", duration_days=days, max_devices=1, price=1)
    db.add(plan)
    db.flush()
    db.add(
        Subscription(
            user_id=user.id,
            plan_id=plan.id,
            type=SubscriptionType.paid,
            expires_at=datetime.now(UTC) + timedelta(days=days),
        )
    )
    db.flush()


# --- entitlement ---------------------------------------------------------


def test_user_without_a_subscription_is_free_tier(db):
    assert required_tier(db, make_user(db)) == NodeTier.free


def test_paying_user_is_paid_tier(db):
    user = make_user(db)
    _subscribe(db, user)
    assert required_tier(db, user) == NodeTier.paid


def test_trial_grants_the_paid_tier(db):
    """The trial exists to show what paying buys, so it must not be shaped."""
    user = make_user(db)
    start_trial(db, user)
    assert required_tier(db, user) == NodeTier.paid


def test_expired_subscription_drops_back_to_free(db):
    user = make_user(db)
    _subscribe(db, user)
    db.query(Subscription).one().expires_at = datetime.now(UTC) - timedelta(days=1)
    db.flush()

    assert required_tier(db, user) == NodeTier.free


# --- placement -----------------------------------------------------------


def test_free_user_lands_on_a_free_node(db, pushes):
    seed_snis(db)
    free_node = make_node(db, tier=NodeTier.free)
    paid_node = make_node(db, tier=NodeTier.paid, host="203.0.113.99")
    make_inbound(db, free_node)
    make_inbound(db, paid_node)

    user = make_user(db)
    assign_config(db, user)

    assert current_tier(db, user) == NodeTier.free


def test_paid_user_lands_on_a_paid_node(db, pushes):
    seed_snis(db)
    free_node = make_node(db, tier=NodeTier.free)
    paid_node = make_node(db, tier=NodeTier.paid, host="203.0.113.99")
    make_inbound(db, free_node)
    make_inbound(db, paid_node)

    user = make_user(db)
    _subscribe(db, user)
    assign_config(db, user)

    assert current_tier(db, user) == NodeTier.paid


def test_free_user_is_refused_rather_than_put_on_a_paid_node(db, pushes):
    """Giving away paid capacity would both undercut the product and eat the
    headroom paying users are promised."""
    seed_snis(db)
    paid_node = make_node(db, tier=NodeTier.paid)
    make_inbound(db, paid_node)

    with pytest.raises(NoCapacityError):
        assign_config(db, make_user(db))


def test_paid_user_falls_back_to_a_free_node_rather_than_getting_nothing(db, pushes):
    seed_snis(db)
    free_node = make_node(db, tier=NodeTier.free)
    make_inbound(db, free_node)

    user = make_user(db)
    _subscribe(db, user)
    assign_config(db, user)

    assert current_tier(db, user) == NodeTier.free, "degraded service beats no service"


# --- reconciliation ------------------------------------------------------


def test_paying_moves_the_user_onto_a_paid_node(db, pushes):
    seed_snis(db)
    free_node = make_node(db, tier=NodeTier.free)
    paid_node = make_node(db, tier=NodeTier.paid, host="203.0.113.99")
    make_inbound(db, free_node)
    make_inbound(db, paid_node)

    user = make_user(db)
    assign_config(db, user)
    assert current_tier(db, user) == NodeTier.free

    _subscribe(db, user)
    moved = reconcile_placement(db, user)

    assert moved is True
    assert current_tier(db, user) == NodeTier.paid


def test_moving_tiers_queues_a_push_so_the_user_is_told(db, pushes):
    seed_snis(db)
    make_inbound(db, make_node(db, tier=NodeTier.free))
    make_inbound(db, make_node(db, tier=NodeTier.paid, host="203.0.113.99"))

    user = make_user(db)
    assign_config(db, user)
    _subscribe(db, user)
    reconcile_placement(db, user)

    push = db.query(ConfigPush).one()
    assert push.user_id == user.id
    assert push.reason == PushReason.tier_changed


def test_expiry_sweep_finds_lapsed_users(db, pushes):
    """Nothing delivers an expiry event, so it has to be swept for."""
    seed_snis(db)
    make_inbound(db, make_node(db, tier=NodeTier.free))
    make_inbound(db, make_node(db, tier=NodeTier.paid, host="203.0.113.99"))

    user = make_user(db)
    _subscribe(db, user)
    assign_config(db, user)
    assert current_tier(db, user) == NodeTier.paid

    db.query(Subscription).one().expires_at = datetime.now(UTC) - timedelta(days=1)
    db.flush()

    assert [u.id for u in users_on_wrong_tier(db)] == [user.id]
    reconcile_placement(db, user)
    assert current_tier(db, user) == NodeTier.free


def test_a_user_already_on_the_right_tier_is_left_alone(db, pushes):
    seed_snis(db)
    make_inbound(db, make_node(db, tier=NodeTier.free))
    user = make_user(db)
    assign_config(db, user)
    before = db.query(Assignment).filter(Assignment.released_at.is_(None)).one().id

    assert reconcile_placement(db, user) is False
    after = db.query(Assignment).filter(Assignment.released_at.is_(None)).one().id
    assert before == after, "no needless reassignment"
    assert db.query(ConfigPush).count() == 0


def test_user_without_any_config_is_not_given_one_by_reconciliation(db, pushes):
    """Minting a config for someone who is not asking loads nodes for nothing."""
    seed_snis(db)
    make_inbound(db, make_node(db, tier=NodeTier.paid))
    user = make_user(db)
    _subscribe(db, user)

    assert reconcile_placement(db, user) is False
    assert db.query(Assignment).count() == 0


def test_control_channel_inbound_is_ignored_when_counting_tier(db, pushes):
    seed_snis(db)
    node = make_node(db, tier=NodeTier.paid)
    make_inbound(db, node, port=8443, is_control_channel=True, control_client_uuid="u")
    make_inbound(db, node, port=443)

    user = make_user(db)
    _subscribe(db, user)
    assign_config(db, user)

    assignment = db.query(Assignment).filter(Assignment.released_at.is_(None)).one()
    inbound = db.get(Inbound, assignment.inbound_id)
    assert inbound.is_control_channel is False
    assert db.get(Node, inbound.node_id).tier == NodeTier.paid
