"""Keeps a user on an inbound that matches what they are entitled to.

Nodes are shared: every node serves free and paying users alike. What
separates them is the inbound, because its port is what `tc` on the node
classifies — paid ports are served first when the link is contended, free
ports take what is left (app/services/tiers.py). So "upgrade a user" means
"move them to a paid-tier inbound", not "move them to another server".

Two events change a user's tier, and both go through `reconcile_placement`
so the paths cannot drift:

  paying      an upgrade should be felt immediately, not at the next
              reconnect, so placement is reconciled as soon as payment is
              confirmed.
  expiring    nobody delivers an expiry event — a subscription simply stops
              being current at a timestamp — so a periodic sweep moves
              lapsed users back down (scheduler.py).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.node import Assignment, Inbound
from app.db.models.outbox import ConfigPush, PushReason
from app.db.models.user import User
from app.services.config_selector import active_assignment, assign_config
from app.services.subscriptions import current_subscription
from app.services.tiers import Tier

logger = logging.getLogger(__name__)


def required_tier(db: Session, user: User) -> Tier:
    """Paid while a subscription (trial included) is live, free otherwise.

    A trial deliberately grants the paid tier: its whole purpose is showing
    what paying buys.
    """
    return Tier.paid if current_subscription(db, user) is not None else Tier.free


def current_tier(db: Session, user: User) -> Tier | None:
    assignment = active_assignment(db, user)
    if assignment is None:
        return None
    inbound = db.get(Inbound, assignment.inbound_id)
    return inbound.tier if inbound else None


def reconcile_placement(db: Session, user: User, *, notify: bool = True) -> bool:
    """Move `user` onto an inbound matching their entitlement. True if moved.

    A user with no assignment at all is left alone: they will be placed on
    the right tier the next time they connect, and minting a config for
    someone who is not asking for one would load nodes for nothing.
    """
    wanted = required_tier(db, user)
    present = current_tier(db, user)
    if present is None or present == wanted:
        return False

    logger.info("moving user %s from %s tier to %s tier", user.id, present.value, wanted.value)
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
    """Everyone whose current inbound tier no longer matches their entitlement.

    Almost always this is expiries, but it also catches a user who was placed
    on the other tier because theirs had no capacity at the time.
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
