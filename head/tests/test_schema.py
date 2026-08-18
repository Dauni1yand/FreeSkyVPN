import uuid

from sqlalchemy import select

from app.db.models.node import Inbound, InboundState, Node
from app.db.models.user import AuthIdentity, AuthProvider, User


def test_schema_round_trips_on_sqlite(db):
    user = User()
    db.add(user)
    db.flush()

    db.add(AuthIdentity(user_id=user.id, provider=AuthProvider.telegram, provider_uid="12345"))
    db.commit()

    identity = db.scalar(select(AuthIdentity).where(AuthIdentity.provider_uid == "12345"))
    assert identity is not None
    assert identity.user_id == user.id


def test_control_channel_inbound_is_distinguishable_from_customer_inbounds(db):
    node = Node(host="203.0.113.10", country="nl")
    db.add(node)
    db.flush()

    control_inbound = Inbound(
        node_id=node.id,
        port=8443,
        sni="www.microsoft.com",
        reality_private_key="priv",
        reality_public_key="pub",
        reality_short_id="ab12",
        is_control_channel=True,
        control_client_uuid=str(uuid.uuid4()),
    )
    customer_inbound = Inbound(
        node_id=node.id,
        port=443,
        sni="www.cloudflare.com",
        reality_private_key="priv2",
        reality_public_key="pub2",
        reality_short_id="cd34",
    )
    db.add_all([control_inbound, customer_inbound])
    db.commit()
    db.refresh(node)

    control_rows = [ib for ib in node.inbounds if ib.is_control_channel]
    assert len(control_rows) == 1
    assert control_rows[0].id == control_inbound.id
    assert customer_inbound.state == InboundState.active
