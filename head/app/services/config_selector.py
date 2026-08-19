"""Decides which node and inbound a user gets — blueprint §07, first half.

The user-facing contract is one button: no country picker, no server list.
So selection is entirely the head's problem, and the only thing that comes
back is a working `vless://` link.

Node eligibility is narrower than "is the node up". A node whose control
channel is `isolated` (node_manager/channel.py) is skipped for *new*
assignments even though it is almost certainly still serving its existing
users perfectly well: we cannot push a new client to a node we cannot
reach, so promising one would hand the user a link that does not work.
Users already on it are untouched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.node import (
    Assignment,
    Inbound,
    InboundState,
    Node,
    NodeChannelState,
    NodeStatus,
)
from app.db.models.user import User
from app.node_manager.exceptions import NodeChannelError
from app.services import keygen
from app.services.inbound_factory import create_inbound
from app.services.node_sync import push_node_config
from app.services.tiers import Tier
from app.services.vless_link import build_vless_link

logger = logging.getLogger(__name__)


class NoCapacityError(RuntimeError):
    """No reachable node could take this user right now."""


@dataclass(frozen=True)
class AssignedConfig:
    vless_url: str
    node_country: str
    inbound_id: str


def active_assignment(db: Session, user: User) -> Assignment | None:
    return db.scalar(
        select(Assignment)
        .where(Assignment.user_id == user.id, Assignment.released_at.is_(None))
        .order_by(Assignment.assigned_at.desc())
    )


def eligible_nodes(
    db: Session, exclude_node_ids: set | None = None, tier: Tier | None = None
) -> list[Node]:
    """Reachable, accepting nodes with room for this tier, least loaded first.

    Every node serves both audiences; what differs is how full a node may be
    before each stops being admitted. Free users stop well below the ceiling
    (`free_admission_ratio`), which is what leaves room for a paying user to
    get in on a busy node instead of finding it full.
    """
    exclude_node_ids = exclude_node_ids or set()
    settings = get_settings()

    load_subq = (
        select(Assignment.inbound_id, func.count().label("n"))
        .where(Assignment.released_at.is_(None))
        .group_by(Assignment.inbound_id)
        .subquery()
    )
    rows = db.execute(
        select(Node, func.coalesce(func.sum(load_subq.c.n), 0).label("load"))
        .outerjoin(Inbound, Inbound.node_id == Node.id)
        .outerjoin(load_subq, load_subq.c.inbound_id == Inbound.id)
        .where(
            Node.status == NodeStatus.active,
            Node.channel_state != NodeChannelState.isolated,
        )
        .group_by(Node.id)
        .order_by(func.coalesce(func.sum(load_subq.c.n), 0).asc())
    ).all()

    nodes = []
    for node, load in rows:
        if node.id in exclude_node_ids:
            continue
        node.load = int(load)  # keep the denormalised counter honest for admin views

        if tier is not None:
            ceiling = node.capacity
            if tier == Tier.grace:
                ceiling = int(node.capacity * settings.free_admission_ratio)
            if node.load >= ceiling:
                continue

        nodes.append(node)
    return nodes


def live_inbound(
    db: Session, node: Node, tier: Tier, exclude_inbound_ids: set | None = None
) -> Inbound | None:
    """A usable inbound of this tier on this node.

    Class matters here rather than at the node: the node's `tc` classes key
    their priority off the inbound's port, so putting a paying user on a
    grace-class inbound would quietly cost them the priority they earned.
    """
    exclude_inbound_ids = exclude_inbound_ids or set()
    inbounds = db.scalars(
        select(Inbound)
        .where(
            Inbound.node_id == node.id,
            Inbound.is_control_channel.is_(False),
            Inbound.state == InboundState.active,
            Inbound.tier == tier,
        )
        .order_by(Inbound.fail_count.asc(), Inbound.created_at.asc())
    ).all()
    return next((ib for ib in inbounds if ib.id not in exclude_inbound_ids), None)


def assign_config(
    db: Session,
    user: User,
    exclude_inbound_ids: set | None = None,
    exclude_node_ids: set | None = None,
) -> AssignedConfig:
    """Place `user` on some working inbound and return their link.

    A failed node is *not* handled by rolling back the transaction: the
    failure bookkeeping `call_node` just wrote (the consecutive-failure
    counters that drive the direct -> tunnel -> isolated ladder) has to
    survive, or a blocked node would reset to zero on every attempt and the
    fallback tunnel would never engage. So an attempt is undone by
    explicitly reversing the rows it added instead.
    """
    from app.services.tiering import required_tier  # tiering imports assign_config

    exclude_inbound_ids = exclude_inbound_ids or set()
    tier = required_tier(db, user)
    nodes = eligible_nodes(db, exclude_node_ids=exclude_node_ids, tier=tier)
    if not nodes:
        # For a free user this is the intended outcome on a busy fleet: the
        # remaining headroom is being held for paying users, which is what
        # "paid goes first under load" means at admission time.
        raise NoCapacityError(
            f"no node is currently accepting {tier.value}-class users"
            + (" — headroom is held back for users on earned access" if tier == Tier.grace else "")
        )

    previous = active_assignment(db, user)
    last_error: Exception | None = None

    for node in nodes:
        created_inbound: Inbound | None = None
        assignment: Assignment | None = None
        try:
            inbound = live_inbound(db, node, tier, exclude_inbound_ids=exclude_inbound_ids)
            if inbound is None:
                inbound = created_inbound = create_inbound(db, node, tier)

            assignment = Assignment(
                user_id=user.id,
                inbound_id=inbound.id,
                xray_uuid=keygen.generate_client_uuid(),
            )
            db.add(assignment)
            if previous is not None:
                previous.released_at = datetime.now(UTC)
            db.flush()

            push_node_config(db, node)

            return AssignedConfig(
                vless_url=build_vless_link(node, inbound, assignment.xray_uuid),
                node_country=node.country,
                inbound_id=str(inbound.id),
            )

        except NodeChannelError as exc:
            logger.warning("node %s unusable while assigning user %s: %s", node.id, user.id, exc)
            last_error = exc
            if assignment is not None:
                db.delete(assignment)
            if previous is not None:
                previous.released_at = None  # user keeps whatever they had
            if created_inbound is not None:
                # it was never accepted by the node, so it does not exist there
                db.delete(created_inbound)
            db.flush()
            continue

    raise NoCapacityError(f"every candidate node failed; last error: {last_error}")
