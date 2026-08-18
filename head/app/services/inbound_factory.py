"""Creates new inbounds — the "программа сама выбирает хороший рабочий порт,
SNI, транспорт" part of the brief.

What each dimension can actually do:

  port       chosen from the set that belongs to the inbound's tier, because
             the node's `tc` classes key their priority off exactly these
             ports (app/services/tiers.py). Preferred list first — all
             ordinary HTTPS ports — then the tier's fallback range.
  SNI        free choice from the curated `sni_candidates` pool, biased away
             from domains that were on recently-killed inbounds.
  transport  constrained — see services/transports.py. Reality permits only
             RAW/XHTTP/gRPC, and RAW+Vision stays the default.

Nothing here talks to the node; the caller pushes the resulting config (see
services/node_sync.py). That split keeps inbound creation a pure database
operation that can be rolled back if the push then fails.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.node import Inbound, InboundState, Node, SniCandidate, SniProbe
from app.services import keygen, tiers
from app.services.tiers import Tier
from app.services.timeutil import as_aware
from app.services.transports import DEFAULT_TRANSPORT, TransportSpec


class NoSniAvailableError(RuntimeError):
    """The sni_candidates pool is empty or fully disabled — an ops problem, not a user-facing one."""


def pick_port(db: Session, node: Node, tier: Tier = Tier.free) -> int:
    """Choose a port for a new inbound on this node.

    Ports are recycled. A dead inbound is dropped from the node's config, so
    its port is genuinely free to bind again, and the supply of ports that
    look natural for TLS is small enough that retiring one permanently after
    a single block would exhaust the preferred list within a handful of
    incidents and push every later inbound onto conspicuous high ports.

    The one thing recycling must not do is hand back the port that was just
    blocked, since that would reproduce the block immediately. So a port
    stays deprioritised while its death is still inside the fail window, and
    becomes an ordinary candidate again afterwards.

    The candidate set is the tier's, never the whole port space: a paid user
    on a port `tc` classes as free would silently lose the priority they are
    paying for.
    """
    settings = get_settings()
    preferred = tiers.ports_for(tier)
    low, high = tiers.fallback_range_for(tier)
    rows = db.execute(
        select(Inbound.port, Inbound.state, Inbound.died_at).where(Inbound.node_id == node.id)
    ).all()

    # A live inbound holds its port open — that one is a hard conflict.
    live_ports = {port for port, state, _ in rows if state != InboundState.dead}

    cutoff = datetime.now(UTC) - timedelta(minutes=settings.inbound_fail_window_minutes)
    recently_burned = {
        port
        for port, state, died_at in rows
        if state == InboundState.dead and (as_aware(died_at) or cutoff) >= cutoff
    }

    for port in preferred:
        if port not in live_ports and port not in recently_burned:
            return port

    # A node with tens of thousands of inbounds is not a scenario worth
    # coding around; a bounded number of attempts then a linear scan is
    # simpler and cannot loop forever.
    for _ in range(50):
        port = random.randint(low, high)
        if port not in live_ports:
            return port
    for port in range(low, high + 1):
        if port not in live_ports:
            return port

    # Last resort: a preferred port that was burned very recently still beats
    # having no inbound to hand the user at all.
    for port in preferred:
        if port not in live_ports:
            return port
    raise RuntimeError(f"no free {tier.value}-tier port left on node {node.id}")


def pick_sni(db: Session, node: Node, exclude: set[str] | None = None) -> SniCandidate:
    """Best candidate for this node, skipping any the caller rules out.

    Preference order:

      1. Domains a recent probe *from this node* found usable, fastest first.
         Reality relays a prober's handshake from the node to this host, so a
         domain verified from anywhere else is a guess.
      2. Anything else in the pool, for a node that has not been probed yet.

    Within each group the least-burned domain wins. Burn count stays a weak
    signal on purpose: a dead inbound never reveals whether its SNI, its port
    or its node's IP was the blocked part, so a burned domain is
    deprioritised rather than retired.
    """
    exclude = exclude or set()
    settings = get_settings()
    fresh_after = datetime.now(UTC) - timedelta(hours=settings.sni_probe_max_age_hours)

    verified = db.execute(
        select(SniCandidate, SniProbe.latency_ms)
        .join(SniProbe, SniProbe.candidate_id == SniCandidate.id)
        .where(
            SniCandidate.active.is_(True),
            SniProbe.node_id == node.id,
            SniProbe.ok.is_(True),
            SniProbe.checked_at >= fresh_after,  # a stale verdict is not a verdict
        )
        .order_by(SniCandidate.burn_count.asc(), SniProbe.latency_ms.asc().nulls_last())
    ).all()

    for candidate, _latency in verified:
        if candidate.domain not in exclude:
            return candidate

    unverified = db.scalars(
        select(SniCandidate)
        .where(SniCandidate.active.is_(True))
        .order_by(SniCandidate.burn_count.asc(), SniCandidate.last_burned_at.asc().nulls_first())
    ).all()

    for candidate in unverified:
        if candidate.domain not in exclude:
            return candidate

    # Every remaining candidate was excluded — fall back to the least-burned
    # one anyway, since a working-but-repeated SNI beats no config at all.
    if verified:
        return verified[0][0]
    if unverified:
        return unverified[0]
    raise NoSniAvailableError(
        "sni_candidates pool is empty; run SNI discovery or seed SNI_SEED_DOMAINS"
    )


def create_inbound(
    db: Session,
    node: Node,
    tier: Tier = Tier.free,
    transport: TransportSpec = DEFAULT_TRANSPORT,
    exclude_snis: set[str] | None = None,
) -> Inbound:
    keypair = keygen.generate_reality_keypair()
    sni = pick_sni(db, node, exclude=exclude_snis)

    inbound = Inbound(
        node_id=node.id,
        tier=tier,
        port=pick_port(db, node, tier),
        sni=sni.domain,
        transport=transport.code,
        reality_private_key=keypair.private_key,
        reality_public_key=keypair.public_key,
        reality_short_id=keygen.generate_short_id(),
    )
    db.add(inbound)
    db.flush()
    return inbound
