"""Two service classes on shared nodes: priority, not segregation.

Every node serves everyone. What separates users is whether the hour they
are using was bought with a watched ad (`full`) or handed out because no ad
could be delivered (`grace`). Two mechanisms express that and both are
tested here:

  bandwidth   the user's inbound decides which `tc` class their traffic
              lands in, so the class must follow the user onto the inbound.
  admission   grace users stop being accepted well before a node is full,
              which leaves room for someone who actually watched an ad to
              get on a busy node instead of finding it full.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.config import get_settings
from app.db.models.node import Assignment, Inbound
from app.db.models.outbox import ConfigPush, PushReason
from app.services import access
from app.services.config_selector import NoCapacityError, assign_config, eligible_nodes
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


def _watch_ad(db, user):
    """Buy an hour the way a real user does."""
    nonce = access.issue_nonce(db, user)
    access.redeem_nonce(db, user, nonce.nonce)
    db.flush()


def _expire(db, user):
    user.access_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()


# --- entitlement ---------------------------------------------------------


def test_user_who_has_not_watched_an_ad_is_grace_class(db):
    assert required_tier(db, make_user(db)) == Tier.grace


def test_watching_an_ad_earns_the_full_class(db):
    user = make_user(db)
    _watch_ad(db, user)
    assert required_tier(db, user) == Tier.full


def test_the_fallback_lands_in_the_grace_class(db):
    """The fallback must not be as good as the thing it stands in for, or it
    becomes the way to skip the ad."""
    user = make_user(db)
    access.grant_grace(db, user)
    assert required_tier(db, user) == Tier.grace


def test_a_lapsed_hour_drops_back_to_grace(db):
    user = make_user(db)
    _watch_ad(db, user)
    _expire(db, user)
    assert required_tier(db, user) == Tier.grace


# --- ports carry the tier ------------------------------------------------


def test_class_port_sets_do_not_overlap():
    """An overlap would put an earned user in the grace tc class at random."""
    assert not set(ports_for(Tier.full)) & set(ports_for(Tier.grace))


@pytest.mark.parametrize("tier", [Tier.grace, Tier.full])
def test_every_class_port_maps_back_to_its_class(tier):
    """The head's ports and the node's tc filters must agree."""
    for port in ports_for(tier):
        assert tier_of_port(port) == tier


def test_new_inbound_gets_a_port_from_its_own_class(db, pushes):
    seed_snis(db)
    make_node(db)
    user = make_user(db)
    _watch_ad(db, user)

    assign_config(db, user)

    inbound = db.query(Assignment).one().inbound
    assert inbound.tier == Tier.full
    assert inbound.port in ports_for(Tier.full)


# --- placement on shared nodes -------------------------------------------


def test_both_classes_share_one_node(db, pushes):
    """No node is reserved for a class."""
    seed_snis(db)
    node = make_node(db)

    grace_user = make_user(db)
    earned_user = make_user(db)
    _watch_ad(db, earned_user)

    assign_config(db, grace_user)
    assign_config(db, earned_user)

    inbounds = {a.user_id: a.inbound for a in db.query(Assignment).all()}
    assert inbounds[grace_user.id].node_id == node.id
    assert inbounds[earned_user.id].node_id == node.id
    assert inbounds[grace_user.id].tier == Tier.grace
    assert inbounds[earned_user.id].tier == Tier.full


def test_paid_and_grace_users_get_separate_inbounds(db, pushes):
    """Sharing an inbound would mean sharing a tc class, losing the priority."""
    seed_snis(db)
    make_node(db)

    grace_user = make_user(db)
    earned_user = make_user(db)
    _watch_ad(db, earned_user)
    assign_config(db, grace_user)
    assign_config(db, earned_user)

    assignments = db.query(Assignment).all()
    assert len({a.inbound_id for a in assignments}) == 2


# --- admission priority --------------------------------------------------


def _fill_node(db, node, count):
    """Occupy `count` slots on `node` with grace-class users."""
    inbound = make_inbound(db, node, port=ports_for(Tier.grace)[0], tier=Tier.grace)
    for _ in range(count):
        make_assignment(db, make_user(db), inbound)


def test_grace_users_stop_being_admitted_before_the_node_is_full(db, pushes):
    """This is the headroom someone who watched an ad gets to use."""
    seed_snis(db)
    node = make_node(db, capacity=10)
    _fill_node(db, node, 8)  # 80% — the free cutoff

    assert eligible_nodes(db, tier=Tier.grace) == []
    assert [n.id for n in eligible_nodes(db, tier=Tier.full)] == [node.id]


def test_paying_user_gets_in_where_a_grace_user_is_turned_away(db, pushes):
    seed_snis(db)
    node = make_node(db, capacity=10)
    _fill_node(db, node, 9)

    with pytest.raises(NoCapacityError):
        assign_config(db, make_user(db))

    earned_user = make_user(db)
    _watch_ad(db, earned_user)
    assign_config(db, earned_user)
    assert current_tier(db, earned_user) == Tier.full


def test_a_genuinely_full_node_turns_away_everyone(db, pushes):
    seed_snis(db)
    node = make_node(db, capacity=5)
    _fill_node(db, node, 5)

    earned_user = make_user(db)
    _watch_ad(db, earned_user)
    with pytest.raises(NoCapacityError):
        assign_config(db, earned_user)


def test_the_refusal_explains_why_capacity_was_withheld(db, pushes):
    seed_snis(db)
    node = make_node(db, capacity=10)
    _fill_node(db, node, 8)

    with pytest.raises(NoCapacityError) as excinfo:
        assign_config(db, make_user(db))
    assert "earned access" in str(excinfo.value)


def test_a_less_loaded_node_is_preferred(db, pushes):
    seed_snis(db)
    busy = make_node(db, host="203.0.113.1", capacity=100)
    quiet = make_node(db, host="203.0.113.2", capacity=100)
    _fill_node(db, busy, 20)

    assign_config(db, make_user(db))

    assert db.query(Assignment).filter(Assignment.released_at.is_(None)).all()[-1].inbound.node_id == quiet.id


# --- reconciliation ------------------------------------------------------


def test_watching_an_ad_moves_the_user_to_a_full_class_inbound(db, pushes):
    seed_snis(db)
    make_node(db)
    user = make_user(db)
    assign_config(db, user)
    assert current_tier(db, user) == Tier.grace

    _watch_ad(db, user)
    assert reconcile_placement(db, user) is True
    assert current_tier(db, user) == Tier.full


def test_moving_class_queues_a_push_so_the_user_is_told(db, pushes):
    seed_snis(db)
    make_node(db)
    user = make_user(db)
    assign_config(db, user)
    _watch_ad(db, user)
    reconcile_placement(db, user)

    push = db.query(ConfigPush).one()
    assert push.user_id == user.id
    assert push.reason == PushReason.tier_changed


def test_expiry_sweep_finds_lapsed_users(db, pushes):
    """Nothing delivers an expiry event — an hour simply stops being current
    at a timestamp — so it has to be swept for."""
    seed_snis(db)
    make_node(db)
    user = make_user(db)
    _watch_ad(db, user)
    assign_config(db, user)
    assert current_tier(db, user) == Tier.full

    _expire(db, user)

    assert [u.id for u in users_on_wrong_tier(db)] == [user.id]
    reconcile_placement(db, user)
    assert current_tier(db, user) == Tier.grace


def test_a_user_already_on_the_right_class_is_left_alone(db, pushes):
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
    _watch_ad(db, user)

    assert reconcile_placement(db, user) is False
    assert db.query(Assignment).count() == 0


def test_control_channel_inbound_is_never_handed_to_a_user(db, pushes):
    seed_snis(db)
    node = make_node(db)
    make_inbound(db, node, port=8443, is_control_channel=True, control_client_uuid="u")

    user = make_user(db)
    _watch_ad(db, user)
    assign_config(db, user)

    inbound = db.get(Inbound, db.query(Assignment).filter(Assignment.released_at.is_(None)).one().inbound_id)
    assert inbound.is_control_channel is False
    assert inbound.tier == Tier.full
