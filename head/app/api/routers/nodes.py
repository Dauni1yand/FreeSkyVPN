"""Node registration and status.

POST /register is called once per node, by provisioning/provision_node.py
right after bootstrap_node.sh has generated Reality keys on the node itself
over SSH — see that script for where these values come from.
"""

from __future__ import annotations

import base64
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.auth import ServiceAuth
from app.api.deps import DbSession
from app.db.models.node import Inbound, Node, NodeChannelState, NodeStatus

router = APIRouter(prefix="/api/v1/nodes", tags=["nodes"], dependencies=[ServiceAuth])


class ControlInboundIn(BaseModel):
    port: int
    sni: str
    reality_private_key: str
    reality_public_key: str
    reality_short_id: str
    control_client_uuid: str


class NodeRegisterRequest(BaseModel):
    host: str
    control_port: int = 62050
    country: str
    # Link capacity tc was configured with; paid traffic is served first out of it.
    uplink_mbit: int | None = None
    capacity: int = 200
    control_inbound: ControlInboundIn
    # base64 of the node's self-signed /var/lib/marzban-node/ssl_cert.pem.
    # Base64 rather than raw PEM because bootstrap_node.sh emits this inside a
    # single-line JSON blob over ssh, where embedded newlines would not survive.
    tls_cert_b64: str


class NodeResponse(BaseModel):
    id: uuid.UUID
    host: str
    country: str
    status: NodeStatus
    channel_state: NodeChannelState
    uplink_mbit: int | None
    capacity: int


@router.post("/register", response_model=NodeResponse)
def register_node(payload: NodeRegisterRequest, db: DbSession) -> NodeResponse:
    try:
        tls_cert_pem = base64.b64decode(payload.tls_cert_b64, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"tls_cert_b64 is not valid base64 PEM: {exc}") from exc

    node = Node(
        host=payload.host,
        control_port=payload.control_port,
        country=payload.country,
        status=NodeStatus.active,
        uplink_mbit=payload.uplink_mbit,
        capacity=payload.capacity,
        tls_cert_pem=tls_cert_pem,
    )
    db.add(node)
    db.flush()

    db.add(
        Inbound(
            node_id=node.id,
            port=payload.control_inbound.port,
            sni=payload.control_inbound.sni,
            transport="reality-vision",
            reality_private_key=payload.control_inbound.reality_private_key,
            reality_public_key=payload.control_inbound.reality_public_key,
            reality_short_id=payload.control_inbound.reality_short_id,
            is_control_channel=True,
            control_client_uuid=payload.control_inbound.control_client_uuid,
        )
    )
    db.commit()
    db.refresh(node)

    return NodeResponse(
        id=node.id,
        host=node.host,
        country=node.country,
        status=node.status,
        channel_state=node.channel_state,
        uplink_mbit=node.uplink_mbit,
        capacity=node.capacity,
    )


@router.get("", response_model=list[NodeResponse])
def list_nodes(db: DbSession) -> list[NodeResponse]:
    nodes = db.scalars(select(Node)).all()
    return [
        NodeResponse(
            id=n.id,
            host=n.host,
            country=n.country,
            status=n.status,
            channel_state=n.channel_state,
            uplink_mbit=n.uplink_mbit,
            capacity=n.capacity,
        )
        for n in nodes
    ]
