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


def run_tier_reconciliation() -> None:
    """Move users whose entitlement no longer matches the node they are on.

    Needed because expiry is not an event anybody delivers: an ad-bought
    hour simply stops being current at a timestamp, and without a sweep a
    lapsed user would keep the priority class until they reconnected.
    """
    from app.services.config_selector import NoCapacityError
    from app.services.tiering import reconcile_placement, users_on_wrong_tier

    with SessionLocal() as db:
        try:
            misplaced = users_on_wrong_tier(db)
        except Exception:
            logger.exception("could not determine misplaced users")
            return

        moved = 0
        for user in misplaced:
            try:
                if reconcile_placement(db, user):
                    moved += 1
                db.commit()
            except NoCapacityError:
                # No node of the right tier right now; try again next sweep.
                db.rollback()
            except Exception:
                db.rollback()
                logger.exception("failed to move user %s to their proper tier", user.id)

        if moved:
            logger.info("moved %d user(s) to their proper tier", moved)


async def tier_reconciliation_loop() -> None:
    interval = get_settings().tier_reconcile_interval_minutes * 60
    while True:
        await asyncio.to_thread(run_tier_reconciliation)
        await asyncio.sleep(interval)


def run_xray_update_check() -> None:
    """Ask the release feed and every node what Xray they are on.

    Only ever *raises proposals* — nothing here restarts anything. Applying
    is a separate pass that runs on approvals, which is the whole point of
    splitting the two (see services/xray_updates.py).
    """
    from app.services import xray_updates

    with SessionLocal() as db:
        try:
            raised = xray_updates.check_for_updates(db)
            db.commit()
            if raised:
                logger.info("raised %d Xray update proposal(s)", len(raised))
        except Exception:
            db.rollback()
            logger.exception("Xray update check failed")


async def xray_update_check_loop() -> None:
    settings = get_settings()
    interval = settings.xray_update_check_interval_hours * 3600
    while True:
        # Network calls and one control-channel round trip per node; keep
        # them off the event loop.
        await asyncio.to_thread(run_xray_update_check)
        await asyncio.sleep(interval)


def run_xray_update_apply() -> None:
    """Apply whatever an operator has approved since the last pass."""
    from app.services import xray_updates

    with SessionLocal() as db:
        try:
            applied = xray_updates.apply_approved(db, limit=get_settings().xray_update_apply_batch)
            db.commit()
            for update in applied:
                logger.info("node %s update finished: %s", update.node_id, update.status.value)
        except Exception:
            db.rollback()
            logger.exception("applying approved Xray updates failed")


async def xray_update_apply_loop() -> None:
    interval = get_settings().xray_update_apply_interval_seconds
    while True:
        await asyncio.to_thread(run_xray_update_apply)
        await asyncio.sleep(interval)


def run_access_expiry() -> None:
    """Disconnect everyone whose bought time has run out.

    The only thing in the system that actually ends a session. Everything
    else — the 402 on connect, the move to the grace class, the app's own
    countdown — gates or presents; this is what removes the user's UUID
    from the node and drops the tunnel.

    Runs often, because the gap between "time is up" and "the VPN stops" is
    free service. A minute of slack is a rounding error; an hour would be
    a sixth of what the ad paid for.
    """
    from app.services.enforcement import sweep_expired

    with SessionLocal() as db:
        try:
            outcome = sweep_expired(db)
            db.commit()
            if outcome.disconnected or outcome.failed_nodes:
                logger.info(
                    "expiry sweep: %d disconnected across %d node(s), %d node(s) unreachable",
                    outcome.disconnected,
                    outcome.nodes_pushed,
                    outcome.failed_nodes,
                )
        except Exception:
            db.rollback()
            logger.exception("access expiry sweep failed")


async def access_expiry_loop() -> None:
    interval = get_settings().access_expiry_interval_seconds
    while True:
        await asyncio.to_thread(run_access_expiry)
        await asyncio.sleep(interval)


def run_node_recovery() -> None:
    """Пробовать ноды, признанные недоступными, — иначе они такими и остаются.

    `call_node` возвращает ноду в строй, как только та ответит: изоляция
    задумывалась как временное состояние. Но выбиралка исключает
    изолированные ноды из кандидатов, а значит обращаться к ним больше
    некому — состояние, снимаемое только успешным вызовом, не получает ни
    одного вызова.

    До сих пор из этого выводила проверка обновлений Xray, которая ходит по
    всем активным нодам раз в двенадцать часов. То есть выздоровевшая нода
    возвращалась в работу в среднем через шесть часов, случайно и незаметно.
    Здесь это делается намеренно и часто.
    """
    from app.db.models.node import Node, NodeChannelState, NodeStatus
    from app.node_manager.channel import call_node
    from app.services.certs import bundle_for

    with SessionLocal() as db:
        try:
            nodes = db.scalars(
                select(Node).where(
                    Node.status == NodeStatus.active,
                    Node.channel_state != NodeChannelState.active,
                )
            ).all()
            for node in nodes:
                was = node.channel_state
                try:
                    call_node(db, node, bundle_for(node), lambda client: client.status())
                except Exception:
                    # call_node уже записал неудачу и подвинул состояние;
                    # здесь нас интересует только обратный переход.
                    logger.debug("node %s still unreachable", node.id, exc_info=True)
                if node.channel_state != was:
                    logger.info(
                        "node %s recovered: %s -> %s",
                        node.id,
                        was.value,
                        node.channel_state.value,
                    )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("node recovery pass failed")


async def node_recovery_loop() -> None:
    interval = get_settings().node_recovery_interval_seconds
    while True:
        await asyncio.to_thread(run_node_recovery)
        await asyncio.sleep(interval)
