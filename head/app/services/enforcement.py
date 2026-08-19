"""Actually cutting off a user whose time has run out.

This is the part that makes the whole model real, and it was missing.
Everything else only gated *issuing* a config: `/me/connect` refuses
without access, and the placement sweep moves a lapsed user to the
lower-priority class. Neither touches a tunnel that is already up.

The node has no idea what a subscription or an hour is. It runs whatever
Xray config it was last given, and that config lists client UUIDs. A user
stays connected for exactly as long as their UUID is in it — which, before
this module, was forever. Watch one ad, keep the VPN indefinitely.

So the enforcement is: release the assignment, re-render the node's config
without that UUID, push it. Xray drops the client on restart and the
tunnel dies. Anything the app does about expiry — a countdown, an
automatic disconnect — is presentation, not enforcement: it runs on the
user's device and a modified build simply would not do it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.node import Assignment, Inbound, Node
from app.db.models.user import User, UserStatus
from app.services import access
from app.services.node_sync import push_node_config

logger = logging.getLogger(__name__)


@dataclass
class ExpirySweep:
    checked: int = 0
    disconnected: int = 0
    nodes_pushed: int = 0
    failed_nodes: int = 0


def users_to_disconnect(db: Session) -> list[tuple[User, Assignment]]:
    """Everyone holding a live config who is no longer entitled to one.

    Two reasons to be here: the hour ran out, or the account was banned.
    Both mean the same thing to the node — the UUID has to go.
    """
    doomed: list[tuple[User, Assignment]] = []

    for assignment in db.scalars(
        select(Assignment).where(Assignment.released_at.is_(None))
    ).all():
        user = db.get(User, assignment.user_id)
        if user is None:
            # The row survived its user; the config still names them.
            continue
        if user.status == UserStatus.banned or not access.has_access(user):
            doomed.append((user, assignment))

    return doomed


def sweep_expired(db: Session) -> ExpirySweep:
    """Release lapsed users and push the shortened config to their nodes.

    Nodes are pushed once each rather than once per user: every push is a
    full config and a restart of that node's Xray, so doing it per user
    would take a busy node down repeatedly for one logical change.

    A node that cannot be reached keeps its assignments. Releasing rows for
    a config we failed to deliver would leave the database claiming someone
    is disconnected while their tunnel is still up, which is worse than
    trying again on the next sweep.
    """
    result = ExpirySweep()
    doomed = users_to_disconnect(db)
    result.checked = len(doomed)
    if not doomed:
        return result

    now = datetime.now(UTC)
    by_node: dict = {}
    for user, assignment in doomed:
        inbound = db.get(Inbound, assignment.inbound_id)
        if inbound is None:
            assignment.released_at = now
            continue
        by_node.setdefault(inbound.node_id, []).append(assignment)

    for node_id, assignments in by_node.items():
        node = db.get(Node, node_id)
        if node is None:
            continue

        for assignment in assignments:
            assignment.released_at = now
        db.flush()

        try:
            push_node_config(db, node)
        except Exception:  # noqa: BLE001 - any failure means the node still serves them
            # Undo: the node still serves these clients, and a database that
            # says otherwise is a database that will never retry.
            for assignment in assignments:
                assignment.released_at = None
            db.flush()
            result.failed_nodes += 1
            logger.warning("could not disconnect %d user(s) from node %s", len(assignments), node_id)
            continue

        result.disconnected += len(assignments)
        result.nodes_pushed += 1
        logger.info("disconnected %d expired user(s) from node %s", len(assignments), node_id)

    db.flush()
    return result
