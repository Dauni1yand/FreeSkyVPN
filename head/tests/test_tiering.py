"""Free/paid on shared nodes: priority, not segregation.

Every node serves both audiences. Two mechanisms express the difference and
both are tested here:

  bandwidth   the user's inbound decides which `tc` class their traffic
              lands in, so the tier must follow the user onto the inbound.
  admission   free users stop being accepted well before a node is full,
              which is what leaves room for a paying user to get on a busy
              node instead of finding it full.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.config import get_settings
from app.db.models.node import Assignment, Inbound
from app.db.models.outbox import ConfigPush, PushReason
from app.db.models.plan import Plan, Subscription, SubscriptionType
from app.services.config_selector import NoCapacityError, assign_config, eligible_nodes
from app.services.subscriptions import start_trial
from app.services.tiering import (
    current_tier,
    reconcile_placement,
    required_tier,
    users_on_wrong_tier,
)
from app.services.tiers import Tier, ports_for, tier_of_port
from tests.factories import (
    make_assignment,
    make_inbound,
    make_node,
    make_user,
    seed_snis,
)


@pytest.fixture(autouse=True)
def _settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
    assert required_tier(db, make_user(db)) == Tier.free


def test_paying_user_is_paid_tier(db):
    user = make_user(db)
    _subscribe(db, user)
    assert required_tier(db, user) == Tier.paid


def test_trial_grants_the_paid_tier(db):
    """The trial exists to show what paying buys, so it must get the priority."""
    user = make_user(db)
    start_trial(db, user)
    assert required_tier(db, user) == Tier.paid


def test_expired_subscription_drops_back_to_free(db):
    user = make_user(db)
    _subscribe(db, user)
    db.query(Subscription).one().expires_at = datetime.now(UTC) - timedelta(days=1)
    db.flush()
    assert required_tier(db, user) == Tier.free


# --- ports carry the tier ------------------------------------------------


def test_tier_port_sets_do_not_overlap():
    """An overlap would put a paying user in the free tc class at random."""
    assert not set(ports_for(Tier.paid)) & set(ports_for(Tier.free))


@pytest.mark.parametrize("tier", [Tier.free, Tier.paid])
def test_every_tier_port_maps_back_to_its_tier(tier):
    """The head's ports and the node's tc filters must agree."""
    for port in ports_for(tier):
        assert tier_of_port(port) == tier


def test_new_inbound_gets_a_port_from_its_own_tier(db, pushes):
    seed_snis(db)
    make_node(db)
    user = make_user(db)
    _subscribe(db, user)

    assign_config(db, user)

    inbound = db.query(Assignment).one().inbound
    assert inbound.tier == Tier.paid
    assert inbound.port in ports_for(Tier.paid)


# --- placement on shared nodes -------------------------------------------


def test_both_tiers_share_one_node(db, pushes):
    """The whole point of the change: no node is reserved for a tier."""
    seed_snis(db)
    node = make_node(db)

    free_user = make_user(db)
    paid_user = make_user(db)
    _subscribe(db, paid_user)

    assign_config(db, free_user)
    assign_config(db, paid_user)

    inbounds = {a.user_id: a.inbound for a in db.query(Assignment).all()}
    assert inbounds[free_user.id].node_id == node.id
    assert inbounds[paid_user.id].node_id == node.id
    assert inbounds[free_user.id].tier == Tier.free
    assert inbounds[paid_user.id].tier == Tier.paid


def test_paid_and_free_users_get_separate_inbounds(db, pushes):
    """Sharing an inbound would mean sharing a tc class, losing the priority."""
    seed_snis(db)
    make_node(db)

    free_user = make_user(db)
    paid_user = make_user(db)
    _subscribe(db, paid_user)
    assign_config(db, free_user)
    assign_config(db, paid_user)

    assignments = db.query(Assignment).all()
    assert len({a.inbound_id for a in assignments}) == 2


# --- admission priority --------------------------------------------------


def _fill_node(db, node, count):
    """Occupy `count` slots on `node` with free users."""
    inbound = make_inbound(db, node, port=ports_for(Tier.free)[0], tier=Tier.free)
    for _ in range(count):
        make_assignment(db, make_user(db), inbound)


def test_free_users_stop_being_admitted_before_the_node_is_full(db, pushes):
    """This is the headroom that a paying user gets to use."""
    seed_snis(db)
    node = make_node(db, capacity=10)
    _fill_node(db, node, 8)  # 80% — the free cutoff

    assert eligible_nodes(db, tier=Tier.free) == []
    assert [n.id for n in eligible_nodes(db, tier=Tier.paid)] == [node.id]


def test_paying_user_gets_in_where_a_free_user_is_turned_away(db, pushes):
    seed_snis(db)
    node = make_node(db, capacity=10)
    _fill_node(db, node, 9)

    with pytest.raises(NoCapacityError):
        assign_config(db, make_user(db))

    paid_user = make_user(db)
    _subscribe(db, paid_user)
    assign_config(db, paid_user)
    assert current_tier(db, paid_user) == Tier.paid


def test_a_genuinely_full_node_turns_away_everyone(db, pushes):
    seed_snis(db)
    node = make_node(db, capacity=5)
    _fill_node(db, node, 5)

    paid_user = make_user(db)
    _subscribe(db, paid_user)
    with pytest.raises(NoCapacityError):
        assign_config(db, paid_user)


def test_the_refusal_explains_why_capacity_was_withheld(db, pushes):
    seed_snis(db)
    node = make_node(db, capacity=10)
    _fill_node(db, node, 8)

    with pytest.raises(NoCapacityError) as excinfo:
        assign_config(db, make_user(db))
    assert "paying" in str(excinfo.value)


def test_a_less_loaded_node_is_preferred(db, pushes):
    seed_snis(db)
    busy = make_node(db, host="203.0.113.1", capacity=100)
    quiet = make_node(db, host="203.0.113.2", capacity=100)
    _fill_node(db, busy, 20)

    assign_config(db, make_user(db))

    assert db.query(Assignment).filter(Assignment.released_at.is_(None)).all()[-1].inbound.node_id == quiet.id


# --- reconciliation ------------------------------------------------------


def test_paying_moves_the_user_to_a_paid_inbound(db, pushes):
    seed_snis(db)
    make_node(db)
    user = make_user(db)
    assign_config(db, user)
    assert current_tier(db, user) == Tier.free

    _subscribe(db, user)
    assert reconcile_placement(db, user) is True
    assert current_tier(db, user) == Tier.paid


def test_moving_tiers_queues_a_push_so_the_user_is_told(db, pushes):
    seed_snis(db)
    make_node(db)
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
    make_node(db)
    user = make_user(db)
    _subscribe(db, user)
    assign_config(db, user)
    assert current_tier(db, user) == Tier.paid

    db.query(Subscription).one().expires_at = datetime.now(UTC) - timedelta(days=1)
    db.flush()

    assert [u.id for u in users_on_wrong_tier(db)] == [user.id]
    reconcile_placement(db, user)
    assert current_tier(db, user) == Tier.free


def test_a_user_already_on_the_right_tier_is_left_alone(db, pushes):
    seed_snis(db)
    make_node(db)
    user = make_user(db)
    assign_config(db, user)
    before = db.query(Assignment).filter(Assignment.released_at.is_(None)).one().id

    assert reconcile_placement(db, user) is False
    after = db.query(Assignment).filter(Assignment.released_at.is_(None)).one().id
    assert before == after
    assert db.query(ConfigPush).count() == 0


def test_user_without_any_config_is_not_given_one_by_reconciliation(db, pushes):
    """Minting a config for someone who is not asking loads nodes for nothing."""
    seed_snis(db)
    make_node(db)
    user = make_user(db)
    _subscribe(db, user)

    assert reconcile_placement(db, user) is False
    assert db.query(Assignment).count() == 0


def test_control_channel_inbound_is_never_handed_to_a_user(db, pushes):
    seed_snis(db)
    node = make_node(db)
    make_inbound(db, node, port=8443, is_control_channel=True, control_client_uuid="u")

    user = make_user(db)
    _subscribe(db, user)
    assign_config(db, user)

    inbound = db.get(Inbound, db.query(Assignment).filter(Assignment.released_at.is_(None)).one().inbound_id)
    assert inbound.is_control_channel is False
    assert inbound.tier == Tier.paid
