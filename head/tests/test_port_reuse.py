from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.db.models.node import InboundState
from app.services.inbound_factory import pick_port
from tests.factories import make_inbound, make_node


def test_live_inbound_ports_are_never_reused(db):
    node = make_node(db)
    make_inbound(db, node, port=443)

    assert pick_port(db, node) == 8443, "a bound port cannot be bound twice"


def test_port_of_a_long_dead_inbound_is_recycled(db):
    node = make_node(db)
    make_inbound(
        db,
        node,
        port=443,
        state=InboundState.dead,
        died_at=datetime.now(UTC) - timedelta(hours=2),
    )

    assert pick_port(db, node) == 443, "a dead inbound frees its port for reuse"


def test_just_burned_port_is_skipped_while_the_window_is_open(db):
    node = make_node(db)
    make_inbound(db, node, port=443, state=InboundState.dead, died_at=datetime.now(UTC))

    picked = pick_port(db, node)

    assert picked != 443, "reusing the port that just got blocked would reproduce the block"
    assert picked == 8443


def test_burned_port_becomes_available_again_once_the_window_lapses(db):
    settings = get_settings()
    node = make_node(db)
    just_outside = datetime.now(UTC) - timedelta(minutes=settings.inbound_fail_window_minutes + 1)
    make_inbound(db, node, port=443, state=InboundState.dead, died_at=just_outside)

    assert pick_port(db, node) == 443


def test_preferred_ports_are_not_exhausted_by_repeated_deaths(db):
    """The point of recycling: a node that has seen many blocks still gets
    ordinary HTTPS ports rather than conspicuous high ones."""
    settings = get_settings()
    node = make_node(db)
    old = datetime.now(UTC) - timedelta(hours=1)
    for port in settings.preferred_ports:
        make_inbound(db, node, port=port, state=InboundState.dead, died_at=old)

    assert pick_port(db, node) in settings.preferred_ports


def test_falls_back_to_a_high_port_when_every_preferred_one_is_live(db):
    settings = get_settings()
    node = make_node(db)
    for port in settings.preferred_ports:
        make_inbound(db, node, port=port)

    picked = pick_port(db, node)

    low, high = settings.fallback_port_range
    assert low <= picked <= high


def test_control_channel_port_is_protected_from_reuse(db):
    """The control channel is never dead, so its port stays occupied."""
    node = make_node(db)
    make_inbound(db, node, port=443)
    make_inbound(db, node, port=8443, is_control_channel=True, control_client_uuid="u")

    assert pick_port(db, node) == 2053
