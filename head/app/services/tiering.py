"""Maps a user's entitlement to the node tier that serves them — phase 4.

Since the speed difference lives in which node a user is on (see
`NodeTier`), keeping the two in step *is* the free/paid split. Two events
move a user between tiers:

  paying      an upgrade should be felt immediately, not at the next
              reconnect, so placement is reconciled as soon as the payment
              is confirmed.
  expiring    nobody tells us the moment a subscription lapses, so a
              periodic sweep moves lapsed users back down (scheduler.py).

Both go through `reconcile_placement`, so the two paths cannot drift.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.node import Assignment, Inbound, Node, NodeTier
from app.db.models.outbox import ConfigPush, PushReason
from app.db.models.user import User
from app.services.config_selector import active_assignment, assign_config
from app.services.subscriptions import current_subscription

logger = logging.getLogger(__name__)


def required_tier(db: Session, user: User) -> NodeTier:
    """Paid while a subscription (trial included) is live, free otherwise.

    A trial deliberately grants the paid tier: its whole purpose is showing
    what paying buys.
    """
    return NodeTier.paid if current_subscription(db, user) is not None else NodeTier.free


def current_tier(db: Session, user: User) -> NodeTier | None:
    assignment = active_assignment(db, user)
    if assignment is None:
        return None
    inbound = db.get(Inbound, assignment.inbound_id)
    node = db.get(Node, inbound.node_id)
    return node.tier


def reconcile_placement(db: Session, user: User, *, notify: bool = True) -> bool:
    """Move `user` onto a node matching their entitlement. True if moved.

    A user with no assignment at all is left alone: they will be placed on
    the right tier the next time they connect, and minting a config for
    someone who is not asking for one would put load on nodes for nothing.
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
    """Everyone whose current node tier no longer matches their entitlement.

    Almost always this is expiries, but it also catches a user who was placed
    on a fallback tier because their proper one had no capacity at the time.
    """
    assignments = db.scalars(select(Assignment).where(Assignment.released_at.is_(None))).all()

    mismatched = []
    for assignment in assignments:
        user = db.get(User, assignment.user_id)
        if user is None:
            continue
        inbound = db.get(Inbound, assignment.inbound_id)
        node = db.get(Node, inbound.node_id)
        if node.tier != required_tier(db, user):
            mismatched.append(user)
    return mismatched
