"""The vless:// link a proxy container is configured with.

provisioning/egress.py turns one link into an Xray client config. It has to
agree with two other pieces of this repository that were written
separately: app/services/vless_link.py, which produces these links, and
android/.../XrayConfigBuilder.kt, which consumes them on the phone. Any
disagreement shows up as a Reality handshake that fails with no useful
message, so the agreement is asserted here rather than discovered later.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest

_EGRESS = Path(__file__).resolve().parents[2] / "provisioning" / "egress.py"
_spec = importlib.util.spec_from_file_location("egress", _EGRESS)
egress = importlib.util.module_from_spec(_spec)
sys.modules["egress"] = egress
_spec.loader.exec_module(egress)


def head_style_link(network="tcp", flow="xtls-rprx-vision", **extra) -> str:
    """Exactly the shape app/services/vless_link.py emits."""
    params = {
        "type": network,
        "security": "reality",
        "sni": "www.microsoft.com",
        "fp": "chrome",
        "pbk": "AbCdEf-PublicKey_123",
        "sid": "a1b2c3d4",
    }
    if flow:
        params["flow"] = flow
    params.update(extra)
    uuid = "11111111-2222-3333-4444-555555555555"
    return f"vless://{uuid}@1.2.3.4:8443?{urlencode(params)}#{quote('FreeSkyVPN')}"


def outbound(link_url: str) -> dict:
    return egress.build_config(egress.parse_vless(link_url), 1080)["outbounds"][0]


def test_a_link_from_the_head_is_understood():
    vnext = outbound(head_style_link())["settings"]["vnext"][0]
    assert vnext["address"] == "1.2.3.4"
    assert vnext["port"] == 8443
    assert vnext["users"][0]["id"] == "11111111-2222-3333-4444-555555555555"
    assert vnext["users"][0]["encryption"] == "none"


def test_reality_parameters_reach_the_config():
    reality = outbound(head_style_link())["streamSettings"]["realitySettings"]
    assert reality == {
        "serverName": "www.microsoft.com",
        "fingerprint": "chrome",
        "publicKey": "AbCdEf-PublicKey_123",
        "shortId": "a1b2c3d4",
        "spiderX": "/",
    }


def test_a_transport_without_a_flow_omits_the_field_entirely():
    """Xray reads an empty flow as a different setting from an absent one."""
    user = outbound(head_style_link(flow=None))["settings"]["vnext"][0]["users"][0]
    assert "flow" not in user


def test_grpc_carries_its_service_name():
    stream = outbound(head_style_link("grpc", None, serviceName="svc-42"))["streamSettings"]
    assert stream["grpcSettings"] == {"serviceName": "svc-42"}


def test_xhttp_carries_its_path():
    stream = outbound(head_style_link("xhttp", None, path="/abc"))["streamSettings"]
    assert stream["xhttpSettings"] == {"path": "/abc"}


def test_the_proxy_listens_only_where_compose_can_reach_it():
    """No published ports plus a plain SOCKS inbound.

    An open SOCKS proxy on a public address is found by scanners within
    hours and becomes somebody else's relay. What keeps that from happening
    is that this service publishes no host ports — asserted in the compose
    file, not here — but the inbound still has to be the plain no-auth one
    the other containers expect.
    """
    inbound = egress.build_config(egress.parse_vless(head_style_link()), 1080)["inbounds"][0]
    assert inbound["port"] == 1080
    assert inbound["protocol"] == "socks"
    assert inbound["settings"] == {"auth": "noauth", "udp": True}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com", "vless://"),
        ("vless://u@h:443?security=tls&sni=a", "reality"),
        ("vless://u@h:443?security=reality&sni=a&sid=c", "pbk"),
        ("vless://u@h:443?security=reality&pbk=b&sid=c", "sni"),
        ("vless://h:443?security=reality&sni=a&pbk=b&sid=c", "uuid"),
    ],
)
def test_an_unusable_link_is_rejected_with_a_reason(url, expected):
    """Naming the missing piece, because the alternative is a silent no-op.

    A proxy that starts with a half-built config fails at connect time,
    which reads as "Telegram is still blocked" rather than "the link was
    pasted wrong".
    """
    with pytest.raises(ValueError, match=expected):
        egress.parse_vless(url)
