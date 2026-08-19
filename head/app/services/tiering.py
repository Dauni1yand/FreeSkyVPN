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

    Full while an ad-bought hour is running. Grace otherwise — including
    for someone whose access has lapsed entirely, because a lapsed user
    with a live connection should degrade rather than be cut off mid-page.
    Refusing them a *new* connection is `/me/connect`'s job, not this one.
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

    A user with no assignment at all is left alone: they will be placed
    correctly the next time they connect, and minting a config for someone
    who is not asking for one would load nodes for nothing.
    """
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
        inbound = db.get(Inbound, assignment.inbound_id)
        if inbound is not None and inbound.tier != required_tier(db, user):
            mismatched.append(user)
    return mismatched
