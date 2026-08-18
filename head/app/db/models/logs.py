import uuid
from datetime import date as date_
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdView(Base):
    """A completed rewarded-video view. See blueprint §07 — Android only, not the bot MVP."""

    __tablename__ = "ad_views"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    watched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reward_minutes: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32), default="rewarded_video")


class FailReport(Base):
    """A user's "не работает" tap. The reactive signal the Config Selector aggregates on."""

    __tablename__ = "fail_reports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    inbound_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("inbounds.id", ondelete="CASCADE"))
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConnectionLog(Base):
    __tablename__ = "connection_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("nodes.id", ondelete="CASCADE"))
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TrafficUsage(Base):
    __tablename__ = "traffic_usage"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("nodes.id", ondelete="CASCADE"))
    date: Mapped[date_] = mapped_column(Date)
    bytes_up: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes_down: Mapped[int] = mapped_column(BigInteger, default=0)


class NodeChannelEvent(Base):
    """Audit trail of control-channel state transitions — active/degraded/isolated.

    Exists so "why did node X go dark on Tuesday" has an answer beyond logs
    scrolling off a server somewhere; see app/node_manager/channel.py.
    """

    __tablename__ = "node_channel_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("nodes.id", ondelete="CASCADE"))
    from_state: Mapped[str] = mapped_column(String(16))
    to_state: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
