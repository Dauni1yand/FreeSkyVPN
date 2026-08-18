"""Port selection: recycled, but never the one that just got blocked, and
never from the other tier's set."""

from datetime import UTC, datetime, timedelta

import pytest

from app.config import get_settings
from app.db.models.node import InboundState
from app.services.inbound_factory import pick_port
from app.services.tiers import Tier, fallback_range_for, ports_for, tier_of_port
from tests.factories import make_inbound, make_node

FREE_PORTS = ports_for(Tier.free)
PAID_PORTS = ports_for(Tier.paid)


def test_live_inbound_ports_are_never_reused(db):
    node = make_node(db)
    make_inbound(db, node, port=FREE_PORTS[0], tier=Tier.free)

    assert pick_port(db, node, Tier.free) == FREE_PORTS[1], "a bound port cannot be bound twice"


def test_port_of_a_long_dead_inbound_is_recycled(db):
    node = make_node(db)
    make_inbound(
        db,
        node,
        port=FREE_PORTS[0],
        tier=Tier.free,
        state=InboundState.dead,
        died_at=datetime.now(UTC) - timedelta(hours=2),
    )

    assert pick_port(db, node, Tier.free) == FREE_PORTS[0], "a dead inbound frees its port"


def test_just_burned_port_is_skipped_while_the_window_is_open(db):
    node = make_node(db)
    make_inbound(
        db, node, port=FREE_PORTS[0], tier=Tier.free, state=InboundState.dead, died_at=datetime.now(UTC)
    )

    picked = pick_port(db, node, Tier.free)

    assert picked != FREE_PORTS[0], "reusing a port that just got blocked reproduces the block"
    assert picked == FREE_PORTS[1]


def test_burned_port_becomes_available_again_once_the_window_lapses(db):
    settings = get_settings()
    node = make_node(db)
    lapsed = datetime.now(UTC) - timedelta(minutes=settings.inbound_fail_window_minutes + 1)
    make_inbound(
        db, node, port=FREE_PORTS[0], tier=Tier.free, state=InboundState.dead, died_at=lapsed
    )

    assert pick_port(db, node, Tier.free) == FREE_PORTS[0]


def test_preferred_ports_are_not_exhausted_by_repeated_deaths(db):
    """The point of recycling: a node that has seen many blocks still gets
    ordinary HTTPS ports rather than conspicuous high ones."""
    node = make_node(db)
    old = datetime.now(UTC) - timedelta(hours=1)
    for port in FREE_PORTS:
        make_inbound(db, node, port=port, tier=Tier.free, state=InboundState.dead, died_at=old)

    assert pick_port(db, node, Tier.free) in FREE_PORTS


def test_falls_back_to_the_tier_range_when_preferred_ports_are_live(db):
    node = make_node(db)
    for port in FREE_PORTS:
        make_inbound(db, node, port=port, tier=Tier.free)

    picked = pick_port(db, node, Tier.free)

    low, high = fallback_range_for(Tier.free)
    assert low <= picked <= high
    assert tier_of_port(picked) == Tier.free, "a fallback port must still land in the right tc class"


@pytest.mark.parametrize("tier", [Tier.free, Tier.paid])
def test_a_tier_never_borrows_the_other_tiers_ports(db, tier):
    """Borrowing would put the user in the wrong tc class and silently
    reverse the priority they were promised."""
    node = make_node(db)
    other = Tier.paid if tier == Tier.free else Tier.free
    # occupy every one of this tier's preferred ports, so it must fall back
    for port in ports_for(tier):
        make_inbound(db, node, port=port, tier=tier)

    picked = pick_port(db, node, tier)

    assert picked not in ports_for(other)
    assert tier_of_port(picked) == tier


def test_the_two_tiers_do_not_collide_on_one_node(db):
    node = make_node(db)
    make_inbound(db, node, port=PAID_PORTS[0], tier=Tier.paid)

    # a paid inbound occupying its port must not push the free tier anywhere
    assert pick_port(db, node, Tier.free) == FREE_PORTS[0]
    assert pick_port(db, node, Tier.paid) == PAID_PORTS[1]


def test_control_channel_port_is_protected_from_reuse(db):
    """The control channel is never dead, so its port stays occupied.

    It sits on 8443, which is also the free tier's first choice — so this is
    the case where the two would collide if liveness were ignored.
    """
    node = make_node(db)
    make_inbound(db, node, port=8443, is_control_channel=True, control_client_uuid="u")

    assert pick_port(db, node, Tier.free) == FREE_PORTS[1]
