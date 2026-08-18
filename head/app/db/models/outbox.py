import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PushReason(str, enum.Enum):
    inbound_blocked = "inbound_blocked"  # the inbound was declared dead for everyone on it
    node_burned = "node_burned"  # the whole node was declared burned, users moved elsewhere
    tier_changed = "tier_changed"  # the user paid or their subscription lapsed, so their node changed


class ConfigPush(Base):
    """Outbox of "this user's config changed, tell them" events.

    The head decides proactively who needs a new config (blueprint §07: a
    blocked inbound is fixed for *everyone* sitting on it, not just whoever
    complained), but the head cannot reach users itself — delivery belongs to
    whichever client the user is on. So the decision is recorded here and the
    delivery layer drains it: the Telegram bot in phase 3, push notifications
    in the Android phase.

    Rows are only created for users who did *not* trigger the change; the
    user who tapped "не работает" already receives their new config in the
    HTTP response.
    """

    __tablename__ = "config_pushes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    assignment_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("assignments.id", ondelete="CASCADE"))
    reason: Mapped[PushReason] = mapped_column(Enum(PushReason, name="push_reason"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)
