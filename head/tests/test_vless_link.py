from urllib.parse import parse_qs, urlparse

from app.services.vless_link import build_vless_link
from tests.factories import make_inbound, make_node


def test_vision_link_carries_reality_params_and_flow(db):
    node = make_node(db, host="203.0.113.7")
    inbound = make_inbound(
        db, node, port=443, sni="www.samsung.com", reality_public_key="PUBKEY", reality_short_id="ab12ab12"
    )

    url = build_vless_link(node, inbound, "the-uuid")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert parsed.scheme == "vless"
    assert parsed.username == "the-uuid"
    assert parsed.hostname == "203.0.113.7"
    assert parsed.port == 443
    assert params["security"] == ["reality"]
    assert params["type"] == ["tcp"]
    assert params["sni"] == ["www.samsung.com"]
    assert params["pbk"] == ["PUBKEY"]
    assert params["sid"] == ["ab12ab12"]
    assert params["flow"] == ["xtls-rprx-vision"]


def test_grpc_link_omits_flow_and_names_the_service(db):
    node = make_node(db)
    inbound = make_inbound(db, node, transport="reality-grpc")

    params = parse_qs(urlparse(build_vless_link(node, inbound, "u")).query)

    assert params["type"] == ["grpc"]
    assert "flow" not in params
    assert params["serviceName"][0], "client and server must agree on the gRPC service name"


def test_link_service_name_matches_the_rendered_server_config(db):
    """The link and the server config are generated separately — they must agree."""
    import json

    from app.node_manager.config_render import render_node_config

    node = make_node(db)
    inbound = make_inbound(db, node, transport="reality-grpc")

    link_params = parse_qs(urlparse(build_vless_link(node, inbound, "u")).query)
    server = json.loads(render_node_config([inbound]))["inbounds"][0]

    assert link_params["serviceName"] == [server["streamSettings"]["grpcSettings"]["serviceName"]]
