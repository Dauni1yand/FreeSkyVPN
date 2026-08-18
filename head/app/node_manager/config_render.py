"""Renders the full Xray config JSON pushed to a node via NodeRestClient.push_config().

marzban-node's control API is coarse (see rest_client.py): there is no
"add one user" call, so every change — a new customer, a revoked one, a
freshly generated inbound after a block — is applied by rebuilding the
whole desired-state config for that node and pushing it wholesale.

Exactly one inbound per node is not a customer inbound: the one with
`is_control_channel=True`, reserved for the head's own Reality-tunnel
fallback (see reality_tunnel.py). It gets a single hardcoded client — the
head itself — and is always included, regardless of the reactive
active/suspect/dead scoring that applies to customer inbounds.
"""

from __future__ import annotations

import json
import logging

from app.db.models.node import Inbound, InboundState
from app.services.transports import DEFAULT_TRANSPORT, get_transport

logger = logging.getLogger(__name__)


def _client_entry(client_id: str, flow: str, email: str | None = None) -> dict:
    entry: dict = {"id": client_id}
    if email is not None:
        entry["email"] = email
    if flow:  # omitted entirely for transports that carry no flow
        entry["flow"] = flow
    return entry


def _inbound_clients(inbound: Inbound, flow: str) -> list[dict]:
    if inbound.is_control_channel:
        if not inbound.control_client_uuid:
            raise ValueError(f"control-channel inbound {inbound.id} has no control_client_uuid")
        return [_client_entry(inbound.control_client_uuid, flow)]

    return [
        _client_entry(assignment.xray_uuid, flow, email=str(assignment.user_id))
        for assignment in inbound.assignments
        if assignment.released_at is None
    ]


def render_node_config(inbounds: list[Inbound]) -> str:
    xray_inbounds = []
    for inbound in inbounds:
        # dead customer inbounds are simply dropped from the next push; the
        # control-channel inbound is never marked dead by that logic
        if inbound.state == InboundState.dead and not inbound.is_control_channel:
            continue

        # The control channel is pinned to the default transport: the head's
        # own tunnel client (reality_tunnel.py) is built for it, and there is
        # no reason to rotate the path the head reaches the node by.
        if inbound.is_control_channel:
            transport = DEFAULT_TRANSPORT
        else:
            try:
                transport = get_transport(inbound.transport)
            except ValueError:
                # Skip rather than raise: this renders the config for the
                # whole node, so letting one unrecognised row abort the push
                # would take every other user on that node down with it.
                logger.error(
                    "inbound %s has unusable transport %r — excluded from node %s config",
                    inbound.id,
                    inbound.transport,
                    inbound.node_id,
                )
                continue

        stream_settings = {
            "network": transport.network,
            "security": "reality",
            "realitySettings": {
                "show": False,
                "dest": f"{inbound.sni}:443",
                "xver": 0,
                "serverNames": [inbound.sni],
                "privateKey": inbound.reality_private_key,
                "shortIds": [inbound.reality_short_id],
            },
            **transport.stream_settings(str(inbound.id)),
        }

        xray_inbounds.append(
            {
                "tag": str(inbound.id),
                "listen": "0.0.0.0",
                "port": inbound.port,
                "protocol": "vless",
                "settings": {"clients": _inbound_clients(inbound, transport.flow), "decryption": "none"},
                "streamSettings": stream_settings,
            }
        )

    config = {
        "log": {"loglevel": "warning"},
        "inbounds": xray_inbounds,
        "outbounds": [{"protocol": "freedom", "tag": "direct"}],
    }
    return json.dumps(config)
