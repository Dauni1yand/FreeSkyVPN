"""Builds the `vless://` URL handed to a client.

Must stay in step with node_manager/config_render.py: whatever the server
side puts in an inbound's streamSettings, the client link has to describe
the same thing or the handshake fails. The transport spec is the shared
source of truth for both.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode

from app.db.models.node import Inbound, Node
from app.services.transports import get_transport


def build_vless_link(node: Node, inbound: Inbound, xray_uuid: str, label: str = "FreeSkyVPN") -> str:
    transport = get_transport(inbound.transport)

    params = {
        "type": transport.network,
        "security": "reality",
        "sni": inbound.sni,
        "fp": "chrome",
        "pbk": inbound.reality_public_key,
        "sid": inbound.reality_short_id,
    }
    if transport.flow:
        params["flow"] = transport.flow

    stream = transport.stream_settings(str(inbound.id))
    if "grpcSettings" in stream:
        params["serviceName"] = stream["grpcSettings"]["serviceName"]
    if "xhttpSettings" in stream:
        params["path"] = stream["xhttpSettings"]["path"]

    query = urlencode(params)
    return f"vless://{xray_uuid}@{node.host}:{inbound.port}?{query}#{quote(label)}"
