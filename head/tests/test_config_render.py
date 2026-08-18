import json
import uuid

from app.db.models.node import Assignment, Inbound, InboundState
from app.node_manager.config_render import render_node_config


def _make_customer_inbound(assignments=(), transport="reality-vision"):
    inbound = Inbound(
        id=uuid.uuid4(),
        node_id=uuid.uuid4(),
        port=443,
        sni="www.cloudflare.com",
        transport=transport,
        reality_private_key="priv",
        reality_public_key="pub",
        reality_short_id="ab12",
    )
    inbound.assignments = list(assignments)
    return inbound


def test_control_channel_inbound_renders_single_hardcoded_client():
    control_uuid = str(uuid.uuid4())
    inbound = Inbound(
        id=uuid.uuid4(),
        node_id=uuid.uuid4(),
        port=8443,
        sni="www.microsoft.com",
        reality_private_key="priv",
        reality_public_key="pub",
        reality_short_id="cd34",
        is_control_channel=True,
        control_client_uuid=control_uuid,
    )

    config = json.loads(render_node_config([inbound]))

    assert len(config["inbounds"]) == 1
    clients = config["inbounds"][0]["settings"]["clients"]
    assert clients == [{"id": control_uuid, "flow": "xtls-rprx-vision"}]


def test_released_assignments_are_excluded_from_customer_inbound():
    active = Assignment(user_id=uuid.uuid4(), xray_uuid=str(uuid.uuid4()), released_at=None)
    released = Assignment(user_id=uuid.uuid4(), xray_uuid=str(uuid.uuid4()))
    released.released_at = "2026-01-01"  # any non-None value marks it released

    inbound = _make_customer_inbound([active, released])

    config = json.loads(render_node_config([inbound]))
    clients = config["inbounds"][0]["settings"]["clients"]
    assert len(clients) == 1
    assert clients[0]["id"] == active.xray_uuid


def test_unusable_transport_excludes_only_that_inbound():
    """One bad row must not take the whole node's config down with it."""
    broken = _make_customer_inbound(transport="reality-carrier-pigeon")
    healthy = _make_customer_inbound()
    healthy.port = 8443

    config = json.loads(render_node_config([broken, healthy]))

    tags = [ib["tag"] for ib in config["inbounds"]]
    assert tags == [str(healthy.id)]


def test_grpc_transport_omits_the_vision_flow():
    """Vision splices a raw TCP stream; under gRPC framing it buys nothing."""
    inbound = _make_customer_inbound(transport="reality-grpc")
    assignment = Assignment(user_id=uuid.uuid4(), xray_uuid=str(uuid.uuid4()), released_at=None)
    inbound.assignments = [assignment]

    config = json.loads(render_node_config([inbound]))

    stream = config["inbounds"][0]["streamSettings"]
    assert stream["network"] == "grpc"
    assert "grpcSettings" in stream
    assert "flow" not in config["inbounds"][0]["settings"]["clients"][0]


def test_dead_customer_inbound_is_dropped_but_control_channel_is_not():
    dead_customer = _make_customer_inbound()
    dead_customer.state = InboundState.dead

    control = Inbound(
        id=uuid.uuid4(),
        node_id=uuid.uuid4(),
        port=8443,
        sni="www.microsoft.com",
        reality_private_key="priv",
        reality_public_key="pub",
        reality_short_id="cd34",
        is_control_channel=True,
        control_client_uuid=str(uuid.uuid4()),
        state=InboundState.dead,  # even if something marked it dead, it must still render
    )

    config = json.loads(render_node_config([dead_customer, control]))
    assert len(config["inbounds"]) == 1
    assert config["inbounds"][0]["tag"] == str(control.id)
