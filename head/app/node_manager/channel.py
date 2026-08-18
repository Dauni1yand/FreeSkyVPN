"""Fault-tolerant channel to a single node's control API.

Three states for the head -> node *control* connection only:

    active    direct REST call succeeds — use it, it's cheap.
    degraded  direct REST has failed `primary_fails_before_fallback` times
              in a row; fall back to tunnelling the same REST call through
              a local Reality client dialling the node's own VPN protocol
              (see reality_tunnel.py) — same camouflage already protecting
              customer traffic, reused instead of reinvented.
    isolated  the tunnelled path *also* failed
              `fallback_fails_before_isolated` times in a row; alert an
              admin over Telegram (the one channel that depends on neither
              path) and keep retrying both on every subsequent call.

A node's already-running Xray process keeps serving its existing users
throughout — nothing here can stop that, since marzban-node's Xray core is
a subprocess that only ever changes on an explicit /restart. Losing the
control channel only blocks *new* pushes (new users, revocations, a fresh
inbound after a block) until the channel recovers.

Switching from direct to tunnelled needs no explicit handoff on the node's
side: marzban-node's REST /connect always evicts whichever caller held the
session before (see rest_client.py), so the new path simply takes over.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.logs import NodeChannelEvent
from app.db.models.node import Inbound, Node, NodeChannelState
from app.node_manager.exceptions import NodeUnreachableError
from app.node_manager.reality_tunnel import RealityTunnel, RealityTunnelParams
from app.node_manager.rest_client import NodeRestClient
from app.node_manager.telegram_signal import notify_admin

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Kept warm per node for as long as this process runs: tearing a tunnel down
# after every call would mean a fresh Reality handshake on every retry,
# which is slow and unnecessarily conspicuous for traffic trying to look
# like an ordinary long-lived VPN session.
_tunnels: dict[str, RealityTunnel] = {}


@dataclass(frozen=True)
class NodeCertBundle:
    """Paths to the head's mTLS identity and the CA it trusts for a node.

    Resolving these (encrypted storage, a secrets manager, whatever the
    deployment ends up using) is deliberately kept outside this module —
    channel.py only needs the three file paths, not where they came from.
    """

    ca_cert: str
    client_cert: str
    client_key: str


def _control_inbound(node: Node) -> Inbound | None:
    return next((ib for ib in node.inbounds if ib.is_control_channel), None)


def _direct_client(node: Node, certs: NodeCertBundle) -> httpx.Client:
    return httpx.Client(
        base_url=f"https://{node.host}:{node.control_port}",
        verify=certs.ca_cert,
        cert=(certs.client_cert, certs.client_key),
    )


def _tunnelled_client(node: Node, certs: NodeCertBundle) -> httpx.Client | None:
    control_inbound = _control_inbound(node)
    if control_inbound is None:
        return None  # node predates the control-channel inbound, or bootstrap skipped it

    tunnel = _tunnels.get(str(node.id))
    if tunnel is None:
        params = RealityTunnelParams(
            node_host=node.host,
            node_port=control_inbound.port,
            sni=control_inbound.sni,
            public_key=control_inbound.reality_public_key,
            short_id=control_inbound.reality_short_id,
            client_uuid=control_inbound.control_client_uuid,
        )
        tunnel = RealityTunnel(params)
        _tunnels[str(node.id)] = tunnel

    local_port = tunnel.start()
    return httpx.Client(
        base_url=f"https://{node.host}:{node.control_port}",
        verify=certs.ca_cert,
        cert=(certs.client_cert, certs.client_key),
        proxy=f"socks5://127.0.0.1:{local_port}",
    )


def _record_transition(db: Session, node: Node, to_state: NodeChannelState, detail: str) -> None:
    if node.channel_state == to_state:
        return
    db.add(
        NodeChannelEvent(
            node_id=node.id,
            from_state=node.channel_state.value,
            to_state=to_state.value,
            detail=detail,
        )
    )
    logger.warning("node %s channel %s -> %s (%s)", node.id, node.channel_state.value, to_state.value, detail)
    node.channel_state = to_state
    node.last_channel_change_at = datetime.now(UTC)
    if to_state == NodeChannelState.isolated:
        notify_admin(
            f"⚠️ Нода {node.country} ({node.host}) изолирована: "
            f"ни прямой канал, ни Reality-туннель недоступны."
        )
    db.flush()


def call_node(db: Session, node: Node, certs: NodeCertBundle, action: Callable[[NodeRestClient], T]) -> T:
    """Run `action(rest_client)` against a node, handling failover.

    `action` describes *what* to do once connected — this function owns
    retries and path selection:

        status = call_node(db, node, certs, lambda c: c.push_config(xray_json))
    """
    settings = get_settings()

    # Try whichever path worked last time first, so a node stuck on the
    # fallback doesn't flap back to a direct attempt (and its timeout) on
    # every single call.
    order = ["direct", "tunnel"] if node.channel_state != NodeChannelState.isolated else ["tunnel", "direct"]
    if node.channel_state == NodeChannelState.degraded:
        order = ["tunnel", "direct"]

    last_error: Exception | None = None
    for path in order:
        client = _direct_client(node, certs) if path == "direct" else _tunnelled_client(node, certs)
        if client is None:
            continue
        try:
            with client:
                rest = NodeRestClient(client, timeout_s=settings.node_channel_primary_timeout_s)
                rest.connect()
                result = action(rest)

            node.last_seen_at = datetime.now(UTC)
            if path == "direct":
                node.consecutive_primary_fails = 0
                _record_transition(db, node, NodeChannelState.active, "прямой канал работает")
            else:
                node.consecutive_fallback_fails = 0
                _record_transition(db, node, NodeChannelState.degraded, "работаем через Reality-туннель")
            db.flush()
            return result

        except Exception as exc:  # noqa: BLE001 - any failure here should trigger fallback, not just network ones
            last_error = exc
            if path == "direct":
                node.consecutive_primary_fails += 1
            else:
                node.consecutive_fallback_fails += 1

    if node.consecutive_fallback_fails >= settings.node_channel_fallback_fails_before_isolated:
        _record_transition(db, node, NodeChannelState.isolated, str(last_error))
    elif node.consecutive_primary_fails >= settings.node_channel_primary_fails_before_fallback:
        _record_transition(db, node, NodeChannelState.degraded, str(last_error))
    db.flush()
    raise NodeUnreachableError(f"node {node.id} unreachable via direct or tunnel: {last_error}")
