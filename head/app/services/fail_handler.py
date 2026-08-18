"""Handles the "не работает" button — blueprint §07, second half.

This is the only detection signal in the system. There is no fleet of
probe hosts inside RF, so blockages are learned from users: one tap is
noise, several taps on the same inbound inside a short window is evidence.

The asymmetry between the two outcomes is the whole point:

  below threshold   only the tapping user is moved. Their problem might be
                    local — their ISP, their device, a transient route.
  at threshold      the inbound is declared dead for *everyone* on it, and
                    every one of those users is migrated and queued for a
                    push. They should not each have to discover the outage
                    and tap the button themselves.

Escalation beyond a single inbound: once a node accumulates several dead
inbounds inside the same window, the node itself is treated as burned and
users are moved off it entirely. A fresh port and SNI cannot rescue a
blocked IP address, and continuing to mint inbounds on a dead node would
just burn SNIs from the pool for nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.logs import FailReport
from app.db.models.node import Assignment, Inbound, InboundState, Node, SniCandidate
from app.db.models.outbox import ConfigPush, PushReason
from app.db.models.user import User
from app.services.config_selector import (
    AssignedConfig,
    active_assignment,
    assign_config,
)

logger = logging.getLogger(__name__)


class NoActiveConfigError(RuntimeError):
    """The user reported a failure but has no config to fail — nothing to act on."""


class ReportTooSoonError(RuntimeError):
    """The user tapped the button again inside the cooldown."""

    def __init__(self, retry_after_seconds: int):
        super().__init__(f"try again in {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class FailureOutcome:
    config: AssignedConfig
    inbound_declared_dead: bool
    node_declared_burned: bool
    users_migrated: int  # including the reporter


def _as_aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes even for timezone-aware columns."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _check_cooldown(db: Session, user: User) -> None:
    cooldown = get_settings().fail_report_cooldown_seconds
    last = db.scalar(
        select(FailReport).where(FailReport.user_id == user.id).order_by(FailReport.reported_at.desc())
    )
    last_at = _as_aware(last.reported_at) if last else None
    if last_at is None:
        return

    elapsed = (datetime.now(UTC) - last_at).total_seconds()
    if elapsed < cooldown:
        raise ReportTooSoonError(retry_after_seconds=int(cooldown - elapsed) + 1)


def _bump_fail_count(inbound: Inbound) -> None:
    """Count the report inside a sliding window, restarting the window when it lapses."""
    settings = get_settings()
    now = datetime.now(UTC)
    window_start = _as_aware(inbound.fail_window_started_at)

    if window_start is None or now - window_start > timedelta(minutes=settings.inbound_fail_window_minutes):
        inbound.fail_window_started_at = now
        inbound.fail_count = 1
    else:
        inbound.fail_count += 1

    if inbound.fail_count > 1 and inbound.state == InboundState.active:
        inbound.state = InboundState.suspect


def _burn_sni(db: Session, domain: str) -> None:
    candidate = db.scalar(select(SniCandidate).where(SniCandidate.domain == domain))
    if candidate is not None:
        candidate.burn_count += 1
        candidate.last_burned_at = datetime.now(UTC)


def _recently_dead_inbound_count(db: Session, node: Node) -> int:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.inbound_fail_window_minutes)
    inbounds = db.scalars(
        select(Inbound).where(Inbound.node_id == node.id, Inbound.state == InboundState.dead)
    ).all()
    return sum(1 for ib in inbounds if (_as_aware(ib.fail_window_started_at) or cutoff) >= cutoff)


def report_failure(db: Session, user: User) -> FailureOutcome:
    assignment = active_assignment(db, user)
    if assignment is None:
        raise NoActiveConfigError("user has no active config")

    _check_cooldown(db, user)

    inbound = db.get(Inbound, assignment.inbound_id)
    node = db.get(Node, inbound.node_id)

    db.add(FailReport(user_id=user.id, inbound_id=inbound.id))
    _bump_fail_count(inbound)
    db.flush()

    settings = get_settings()
    threshold_reached = inbound.fail_count >= settings.inbound_fail_threshold

    if not threshold_reached:
        # Individual case: move just this user, and keep them off the inbound
        # they are complaining about.
        config = assign_config(db, user, exclude_inbound_ids={inbound.id})
        return FailureOutcome(
            config=config, inbound_declared_dead=False, node_declared_burned=False, users_migrated=1
        )

    logger.warning("inbound %s declared dead after %d reports", inbound.id, inbound.fail_count)
    inbound.state = InboundState.dead
    _burn_sni(db, inbound.sni)
    db.flush()

    node_burned = _recently_dead_inbound_count(db, node) >= settings.node_dead_inbound_threshold
    if node_burned:
        logger.warning("node %s declared burned — migrating its users away", node.id)

    excluded_nodes = {node.id} if node_burned else set()
    reason = PushReason.node_burned if node_burned else PushReason.inbound_blocked

    # The reporter first, so their new config is what the HTTP response carries.
    config = assign_config(db, user, exclude_inbound_ids={inbound.id}, exclude_node_ids=excluded_nodes)
    migrated = 1

    stranded = db.scalars(
        select(Assignment).where(
            Assignment.inbound_id == inbound.id,
            Assignment.released_at.is_(None),
            Assignment.user_id != user.id,
        )
    ).all()

    for other in stranded:
        other_user = db.get(User, other.user_id)
        try:
            assign_config(db, other_user, exclude_inbound_ids={inbound.id}, exclude_node_ids=excluded_nodes)
        except Exception:
            # One user's migration failing must not strand the rest; they
            # keep their (dead) assignment and will be picked up when they
            # tap the button themselves.
            logger.exception("failed to migrate user %s off dead inbound %s", other.user_id, inbound.id)
            continue

        new_assignment = active_assignment(db, other_user)
        db.add(ConfigPush(user_id=other_user.id, assignment_id=new_assignment.id, reason=reason))
        migrated += 1

    db.flush()
    return FailureOutcome(
        config=config,
        inbound_declared_dead=True,
        node_declared_burned=node_burned,
        users_migrated=migrated,
    )
