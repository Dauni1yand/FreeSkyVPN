"""Pushes a node's desired state to it.

Ordering matters and is deliberate: callers flush their DB changes first,
then call this, then commit. If the push fails the transaction rolls back
and the node was never touched. If the push succeeds but the commit then
fails, the node briefly holds a client the database does not know about —
harmless, since the next full push (every change is a full push) removes it.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.node import Inbound, Node
from app.node_manager.channel import call_node
from app.node_manager.config_render import render_node_config
from app.services.certs import bundle_for

logger = logging.getLogger(__name__)


def push_node_config(db: Session, node: Node) -> None:
    inbounds = db.scalars(select(Inbound).where(Inbound.node_id == node.id)).all()
    config_json = render_node_config(list(inbounds))
    certs = bundle_for(node)

    logger.info("pushing config to node %s (%d inbounds)", node.id, len(inbounds))
    call_node(db, node, certs, lambda client: client.push_config(config_json))
