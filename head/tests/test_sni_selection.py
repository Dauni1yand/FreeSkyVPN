"""Selection must prefer what a node actually verified, not the pool order."""

from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.db.models.node import SniCandidate, SniProbe
from app.services.inbound_factory import pick_sni
from tests.factories import make_node


def _candidate(db, domain, burn_count=0):
    candidate = SniCandidate(domain=domain, burn_count=burn_count)
    db.add(candidate)
    db.flush()
    return candidate


def _probe(db, node, candidate, ok=True, latency_ms=100, age_hours=0):
    db.add(
        SniProbe(
            node_id=node.id,
            candidate_id=candidate.id,
            ok=ok,
            latency_ms=latency_ms,
            from_node=True,
            checked_at=datetime.now(UTC) - timedelta(hours=age_hours),
        )
    )
    db.flush()


def test_verified_candidate_beats_an_unprobed_one(db):
    node = make_node(db)
    _candidate(db, "unprobed.com")
    verified = _candidate(db, "verified.com")
    _probe(db, node, verified)

    assert pick_sni(db, node).domain == "verified.com"


def test_fastest_verified_candidate_wins(db):
    node = make_node(db)
    slow = _candidate(db, "slow.com")
    fast = _candidate(db, "fast.com")
    _probe(db, node, slow, latency_ms=800)
    _probe(db, node, fast, latency_ms=30)

    assert pick_sni(db, node).domain == "fast.com"


def test_a_candidate_that_failed_probing_is_not_offered(db):
    node = make_node(db)
    broken = _candidate(db, "broken.com")
    _probe(db, node, broken, ok=False, latency_ms=None)

    # nothing else verified, so it falls back to the unverified pool — which
    # still contains this domain, but only because there is no alternative
    other = _candidate(db, "other.com")
    _probe(db, node, other, ok=True, latency_ms=50)

    assert pick_sni(db, node).domain == "other.com"


def test_stale_verdicts_do_not_count_as_verified(db):
    node = make_node(db)
    settings = get_settings()
    stale = _candidate(db, "stale.com")
    _probe(db, node, stale, latency_ms=10, age_hours=settings.sni_probe_max_age_hours + 1)
    fresh = _candidate(db, "fresh.com")
    _probe(db, node, fresh, latency_ms=900)

    assert pick_sni(db, node).domain == "fresh.com", "a week-old verdict is not evidence"


def test_verdicts_from_another_node_do_not_transfer(db):
    """Reachability and latency are properties of the node-to-domain path."""
    node_a = make_node(db, country="nl", host="203.0.113.1")
    node_b = make_node(db, country="sg", host="203.0.113.2")
    only_good_for_a = _candidate(db, "close-to-a.com")
    _probe(db, node_a, only_good_for_a, latency_ms=5)

    picked_for_b = pick_sni(db, node_b)

    # b has no verdicts at all, so it falls back to the pool rather than
    # inheriting a's measurement as if it were its own
    assert picked_for_b.domain == "close-to-a.com"
    b_probes = db.query(SniProbe).filter(SniProbe.node_id == node_b.id).count()
    assert b_probes == 0


def test_least_burned_wins_among_verified(db):
    node = make_node(db)
    burned = _candidate(db, "burned.com", burn_count=3)
    clean = _candidate(db, "clean.com", burn_count=0)
    _probe(db, node, burned, latency_ms=10)
    _probe(db, node, clean, latency_ms=500)

    assert pick_sni(db, node).domain == "clean.com", "a repeatedly blocked domain loses to a slower clean one"


def test_excluded_domain_is_skipped(db):
    node = make_node(db)
    first = _candidate(db, "first.com")
    second = _candidate(db, "second.com")
    _probe(db, node, first, latency_ms=10)
    _probe(db, node, second, latency_ms=20)

    assert pick_sni(db, node, exclude={"first.com"}).domain == "second.com"
