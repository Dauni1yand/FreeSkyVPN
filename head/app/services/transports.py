"""Which transports an inbound may actually use, and what each implies.

The brief called for rotating "порт, SNI, транспорт" as the situation
demands. Port and SNI rotate freely. Transport does not: Reality constrains
the set. Verified against Xray-core 26.3.27 — a Reality inbound over
WebSocket is rejected outright at config load:

    infra/conf: REALITY only supports RAW, XHTTP and gRPC for now.

So the rotation space is RAW (a.k.a. tcp), XHTTP and gRPC — ws and
httpupgrade are not options at all while we stay on Reality.

Within that set, `xtls-rprx-vision` is kept only on RAW. The config parser
does accept flow=vision alongside gRPC/XHTTP, but Vision's whole point is
splicing a raw TCP stream; layering it under gRPC or XHTTP framing buys
nothing and is not how either is deployed in practice. Non-RAW variants
therefore ship with no flow, which is the ordinary configuration for them.

RAW+Vision stays the default for every inbound. The alternates exist for
the case where a network turns out to interfere with plain Reality-over-TCP
specifically; they are not better disguises in general.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class TransportSpec:
    code: str  # stored in Inbound.transport
    network: str  # xray streamSettings.network
    flow: str  # per-client flow; "" means omit the field entirely

    def stream_settings(self, inbound_id: str) -> dict:
        """Transport-specific streamSettings keys.

        gRPC serviceName and XHTTP path are derived from the inbound's id
        rather than stored: they must be stable for the lifetime of the
        inbound (client and server have to agree) but carry no meaning
        beyond that, so a hash of the id is enough and saves a column.
        """
        if self.network == "grpc":
            return {"grpcSettings": {"serviceName": _derived_token(inbound_id)}}
        if self.network == "xhttp":
            return {"xhttpSettings": {"path": f"/{_derived_token(inbound_id)}"}}
        return {}


def _derived_token(inbound_id: str) -> str:
    return hashlib.sha256(inbound_id.encode()).hexdigest()[:16]


# "tcp" rather than "raw": Xray accepts both (raw is the newer name for the
# same network) and older cores, which some nodes may still be running,
# only know "tcp".
REALITY_VISION = TransportSpec(code="reality-vision", network="tcp", flow="xtls-rprx-vision")
REALITY_GRPC = TransportSpec(code="reality-grpc", network="grpc", flow="")
REALITY_XHTTP = TransportSpec(code="reality-xhttp", network="xhttp", flow="")

TRANSPORTS: dict[str, TransportSpec] = {
    spec.code: spec for spec in (REALITY_VISION, REALITY_GRPC, REALITY_XHTTP)
}

DEFAULT_TRANSPORT = REALITY_VISION


def get_transport(code: str) -> TransportSpec:
    try:
        return TRANSPORTS[code]
    except KeyError:
        raise ValueError(f"unknown transport {code!r}; known: {sorted(TRANSPORTS)}") from None
