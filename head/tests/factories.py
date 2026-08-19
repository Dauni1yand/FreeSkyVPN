"""Small builders so tests read as scenarios rather than as ORM setup."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models.node import Assignment, Inbound, Node, SniCandidate
from app.db.models.user import User
from app.services.tiers import Tier, tier_of_port


def make_user(db: Session) -> User:
    user = User()
    db.add(user)
    db.flush()
    return user


def make_node(db: Session, country: str = "nl", **kwargs) -> Node:
    node = Node(
        host=kwargs.pop("host", f"203.0.113.{len(db.query(Node).all()) + 10}"),
        country=country,
        tls_cert_pem=kwargs.pop("tls_cert_pem", "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----"),
        **kwargs,
    )
    db.add(node)
    db.flush()
    return node


def make_inbound(db: Session, node: Node, **kwargs) -> Inbound:
    """Build an inbound whose port and class agree.

    The class defaults to whichever one owns the port rather than to a
    fixed value: a row with port 443 and the grace class cannot exist on a
    real node — `tc` classifies by port — so a factory that can produce one
    only manufactures failures that mean nothing.
    """
    port = kwargs.pop("port", 443)
    inbound = Inbound(
        node_id=node.id,
        port=port,
        sni=kwargs.pop("sni", "www.samsung.com"),
        transport=kwargs.pop("transport", "reality-vision"),
        tier=kwargs.pop("tier", tier_of_port(port) or Tier.grace),
        reality_private_key=kwargs.pop("reality_private_key", "priv"),
        reality_public_key=kwargs.pop("reality_public_key", "pub"),
        reality_short_id=kwargs.pop("reality_short_id", "ab12ab12"),
        **kwargs,
    )
    db.add(inbound)
    db.flush()
    return inbound


def make_assignment(db: Session, user: User, inbound: Inbound) -> Assignment:
    assignment = Assignment(user_id=user.id, inbound_id=inbound.id, xray_uuid=str(uuid.uuid4()))
    db.add(assignment)
    db.flush()
    return assignment


def seed_snis(db: Session, domains: list[str] | None = None) -> None:
    for domain in domains or ["www.samsung.com", "www.nvidia.com", "www.asus.com"]:
        db.add(SniCandidate(domain=domain))
    db.flush()
