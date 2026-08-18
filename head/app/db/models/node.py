import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class NodeStatus(str, enum.Enum):
    active = "active"
    draining = "draining"


class NodeTier(str, enum.Enum):
    """Which audience a node serves.

    Xray-core has no per-user bandwidth limit (verified by measurement — the
    `speedLimit` policy field found in various guides is silently ignored,
    behaving exactly like a field that does not exist). Separating the tiers
    onto different nodes is therefore how "free is slower, paid gets priority
    and full speed" is delivered: a free node is shaped once with `tc` at
    provisioning time, a paid node is not, and no per-user classification or
    runtime execution on the node is needed for either.
    """

    free = "free"
    paid = "paid"


class NodeChannelState(str, enum.Enum):
    """State of the head -> node *control* channel — independent of whether the
    node's Xray is actually serving users, which keeps running regardless."""

    active = "active"  # direct REST call to the node works
    degraded = "degraded"  # direct path is failing, routed through the Reality tunnel instead
    isolated = "isolated"  # both direct and tunnelled paths are failing


class InboundState(str, enum.Enum):
    active = "active"
    suspect = "suspect"
    dead = "dead"


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    host: Mapped[str] = mapped_column(String(255))
    control_port: Mapped[int] = mapped_column(Integer, default=62050)  # marzban-node SERVICE_PORT
    country: Mapped[str] = mapped_column(String(64))
    status: Mapped[NodeStatus] = mapped_column(Enum(NodeStatus, name="node_status"), default=NodeStatus.active)

    tier: Mapped[NodeTier] = mapped_column(Enum(NodeTier, name="node_tier"), default=NodeTier.free)
    # The rate `tc` was configured with at provisioning time, recorded so the
    # head can report what a free user actually gets. Null on paid nodes,
    # which are left unshaped.
    shaped_mbit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # marzban-node generates its own self-signed cert on first boot; the head
    # captures it during provisioning and pins it as the only cert it will
    # accept from this node. Without it there is nothing to verify the TLS
    # peer against, so this is required for the control channel to work at all.
    tls_cert_pem: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- control-channel resilience state (see app/node_manager/channel.py) ---
    channel_state: Mapped[NodeChannelState] = mapped_column(
        Enum(NodeChannelState, name="node_channel_state"), default=NodeChannelState.active
    )
    consecutive_primary_fails: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_fallback_fails: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_channel_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    inbounds: Mapped[list["Inbound"]] = relationship(back_populates="node", cascade="all, delete-orphan")


class Inbound(Base):
    """One port/SNI/transport combination on a node.

    Almost all rows here are handed out to paying/free users by the Config
    Selector (phase 2). Exactly one row per node is different:
    `is_control_channel=True` marks the dedicated Reality inbound that
    exists solely so the head can tunnel its control-plane REST calls
    through it when the direct path is blocked (see reality_tunnel.py). It
    is never assigned to a user, never scored by the fail_count/state
    reactive-blocking logic in the roadmap's Config Selector phase, and is
    always included when rendering a node's Xray config.
    """

    __tablename__ = "inbounds"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("nodes.id", ondelete="CASCADE"))
    port: Mapped[int] = mapped_column(Integer)
    sni: Mapped[str] = mapped_column(String(255))
    transport: Mapped[str] = mapped_column(String(32), default="reality-vision")

    # reality_private_key is sensitive: it must be embedded in every full
    # config push (marzban-node has no "add one user" call — see
    # config_render.py), so unlike a pure zero-knowledge design the head
    # necessarily holds a copy after bootstrap. Encrypt this column at rest.
    reality_private_key: Mapped[str] = mapped_column(String(255))
    reality_public_key: Mapped[str] = mapped_column(String(255))  # handed to clients / used by the tunnel
    reality_short_id: Mapped[str] = mapped_column(String(32))

    state: Mapped[InboundState] = mapped_column(Enum(InboundState, name="inbound_state"), default=InboundState.active)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When this inbound was declared dead. A dead inbound is dropped from the
    # node's config, which frees its port for reuse — but a port that died
    # moments ago is a poor choice for the replacement, so the timestamp
    # drives how long it stays deprioritised (see inbound_factory.pick_port).
    died_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    is_control_channel: Mapped[bool] = mapped_column(Boolean, default=False)
    control_client_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)

    node: Mapped["Node"] = relationship(back_populates="inbounds")
    assignments: Mapped[list["Assignment"]] = relationship(back_populates="inbound", cascade="all, delete-orphan")


class Assignment(Base):
    """Which user currently sits on which inbound."""

    __tablename__ = "assignments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    inbound_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("inbounds.id", ondelete="CASCADE"))
    xray_uuid: Mapped[str] = mapped_column(String(36))  # this user's VLESS client id on this inbound
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    inbound: Mapped["Inbound"] = relationship(back_populates="assignments")


class SniCandidate(Base):
    """Curated pool of domains usable as a Reality `serverName`/`dest`.

    Kept in data rather than code for the same reason as `plans`: which
    domains still pass unblocked is an operational question that changes
    faster than deploys. A usable candidate must be a real site that speaks
    TLS 1.3 + HTTP/2, is reachable from the target audience, and is not ours
    — Reality forwards non-authenticated probes there, so the deception only
    holds if the domain genuinely answers.

    `burn_count` records how often an inbound using this SNI was declared
    dead. It only biases selection (least-recently-burned first) rather than
    auto-disabling, because a dead inbound never tells us *which* of its
    port, SNI or node IP was the part that got blocked.
    """

    __tablename__ = "sni_candidates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    domain: Mapped[str] = mapped_column(String(255), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    burn_count: Mapped[int] = mapped_column(Integer, default=0)
    last_burned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "auto" for anything discovery pulled from a popularity ranking,
    # "static" for domains an operator added by hand.
    source: Mapped[str] = mapped_column(String(32), default="static")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    probes: Mapped[list["SniProbe"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")


class SniProbe(Base):
    """One node's verdict on one candidate domain.

    Per node rather than global because the measurement is node-relative:
    Reality relays a prober's handshake from the node to this host, so
    reachability and latency are properties of the node-to-domain path, and
    a domain that is fast from Amsterdam may be useless from Singapore.
    """

    __tablename__ = "sni_probes"
    __table_args__ = (UniqueConstraint("node_id", "candidate_id", name="uq_sni_probe_node_candidate"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("nodes.id", ondelete="CASCADE"))
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sni_candidates.id", ondelete="CASCADE")
    )

    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    tls_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    alpn: Mapped[str | None] = mapped_column(String(16), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # False when the probe had to be taken from the head because no tunnel to
    # the node could be opened — the verdict is then weaker than it looks.
    from_node: Mapped[bool] = mapped_column(Boolean, default=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    candidate: Mapped["SniCandidate"] = relationship(back_populates="probes")
