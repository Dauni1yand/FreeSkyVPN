"""Keeps a user on an inbound that matches what they currently have.

Nodes are shared: every node serves everyone. What separates users is the
inbound, because its port is what `tc` on the node classifies — the full
class is served first when the link is contended, the grace class takes
what is left (app/services/tiers.py). So "upgrade a user" means "move them
to a full-class inbound", not "move them to another server".

Two events change which class a user belongs in, and both come through
`reconcile_placement` so the paths cannot drift:

  watched an ad   the hour they just bought should be felt immediately, not
                  at the next reconnect.
  hour ran out    nobody delivers an expiry event — access simply stops
                  being current at a timestamp — so a periodic sweep moves
                  lapsed users down (scheduler.py).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.node import Assignment, Inbound
from app.db.models.outbox import ConfigPush, PushReason
from app.db.models.user import User
from app.services import access
from app.services.config_selector import active_assignment, assign_config
from app.services.tiers import Tier

logger = logging.getLogger(__name__)


def required_tier(db: Session, user: User) -> Tier:
    """The class this user has earned right now.

    Full while an ad-bought hour is running, grace while the fallback is.
    Someone with no access at all still answers `grace` here — the function
    describes a class, and every user has to map to one — but that is not
    the same as saying they belong online, and `reconcile_placement` below
    is careful about the difference.
    """
    state = access.state_of(user)
    return Tier.full if state.active and not state.is_grace else Tier.grace


def current_tier(db: Session, user: User) -> Tier | None:
    assignment = active_assignment(db, user)
    if assignment is None:
        return None
    inbound = db.get(Inbound, assignment.inbound_id)
    return inbound.tier if inbound else None


def reconcile_placement(db: Session, user: User, *, notify: bool = True) -> bool:
    """Move `user` onto an inbound matching their class. True if moved.

    Two people are deliberately left alone.

    A user with no assignment at all: they will be placed correctly the next
    time they connect, and minting a config for someone who is not asking
    for one would load nodes for nothing.

    A user whose time has run out: they belong offline, not in a slower
    class, and moving them is actively wrong. `required_tier` answers
    `grace` for them because every user has to map to some class, and
    acting on that answer used to mean an expired user got a *fresh* config
    — a new inbound, a node restart, and an outbox message telling them
    their config had changed — moments before the expiry sweep disconnected
    them anyway. Removing them is `enforcement.sweep_expired`'s job.
    """
    if not access.has_access(user):
        return False

    wanted = required_tier(db, user)
    present = current_tier(db, user)
    if present is None or present == wanted:
        return False

    logger.info("moving user %s from %s to %s class", user.id, present.value, wanted.value)
    assign_config(db, user)

    if notify:
        moved = active_assignment(db, user)
        if moved is not None:
            db.add(
                ConfigPush(
                    user_id=user.id,
                    assignment_id=moved.id,
                    reason=PushReason.tier_changed,
                )
            )
    db.flush()
    return True


def users_on_wrong_tier(db: Session) -> list[User]:
    """Everyone whose inbound no longer matches the class they have earned.

    Almost always this is hours running out, but it also catches a user who
    landed on the other class because theirs had no capacity at the time.
    """
    assignments = db.scalars(select(Assignment).where(Assignment.released_at.is_(None))).all()

    mismatched = []
    for assignment in assignments:
        user = db.get(User, assignment.user_id)
        if user is None:
            continue
        # Expired users are the expiry sweep's business. Listing them here
        # would have this loop reconnect the very people that one is about
        # to cut off.
        if not access.has_access(user):
            continue
        inbound = db.get(Inbound, assignment.inbound_id)
        if inbound is not None and inbound.tier != required_tier(db, user):
            mismatched.append(user)
    return mismatched
