"""Background upkeep that has to happen without anyone asking.

Currently one job: keeping the SNI pool current. Candidate domains go stale
(a site drops h2, changes hosting, or gets blocked), and a pool refreshed
only when an operator remembers to is a pool that silently rots. Probing is
also per node, so it has to re-run as nodes are added.

Deliberately a plain asyncio task rather than Celery or APScheduler: there
is one periodic job, it is idempotent, and losing a tick costs nothing —
the next one repeats the work. A missed run is not an incident.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.config import get_settings
from app.db.models.node import Node, NodeChannelState, NodeStatus
from app.db.session import SessionLocal
from app.services.sni_discovery import (
    default_sources,
    probe_candidates_for_node,
    refresh_candidates,
)

logger = logging.getLogger(__name__)


def run_sni_maintenance() -> None:
    """One pass: top up the candidate pool, then re-probe it from every node."""
    with SessionLocal() as db:
        try:
            added = refresh_candidates(db, default_sources())
            db.commit()
            logger.info("SNI pool refreshed, %d new candidates", added)
        except Exception:
            db.rollback()
            logger.exception("SNI candidate refresh failed")

        nodes = db.scalars(
            select(Node).where(
                Node.status == NodeStatus.active,
                # An isolated node cannot be tunnelled to, so probes through
                # it would silently fall back to the head's vantage point and
                # record a weaker verdict as if it were a node-side one.
                Node.channel_state != NodeChannelState.isolated,
            )
        ).all()

        for node in nodes:
            try:
                probe_candidates_for_node(db, node)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("SNI probing failed for node %s", node.id)


async def sni_maintenance_loop() -> None:
    settings = get_settings()
    interval = settings.sni_refresh_interval_hours * 3600
    while True:
        # Probing is blocking socket work; keep it off the event loop so it
        # cannot stall request handling.
        await asyncio.to_thread(run_sni_maintenance)
        await asyncio.sleep(interval)
