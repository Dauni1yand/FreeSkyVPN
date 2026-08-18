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

from app.db.models.node import Inbound, InboundState


def _inbound_clients(inbound: Inbound) -> list[dict]:
    if inbound.is_control_channel:
        if not inbound.control_client_uuid:
            raise ValueError(f"control-channel inbound {inbound.id} has no control_client_uuid")
        return [{"id": inbound.control_client_uuid, "flow": "xtls-rprx-vision"}]

    return [
        {"id": assignment.xray_uuid, "email": str(assignment.user_id), "flow": "xtls-rprx-vision"}
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

        xray_inbounds.append(
            {
                "tag": str(inbound.id),
                "listen": "0.0.0.0",
                "port": inbound.port,
                "protocol": "vless",
                "settings": {"clients": _inbound_clients(inbound), "decryption": "none"},
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "show": False,
                        "dest": f"{inbound.sni}:443",
                        "xver": 0,
                        "serverNames": [inbound.sni],
                        "privateKey": inbound.reality_private_key,
                        "shortIds": [inbound.reality_short_id],
                    },
                },
            }
        )

    config = {
        "log": {"loglevel": "warning"},
        "inbounds": xray_inbounds,
        "outbounds": [{"protocol": "freedom", "tag": "direct"}],
    }
    return json.dumps(config)
