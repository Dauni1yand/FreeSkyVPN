"""Xray version updates, from detection through to an operator's decision.

One row is one proposal: "node X could move from version A to version B".
It exists because updating Xray is not a background chore — recreating the
node's container drops every live connection on it, so the head is allowed
to *notice* an update on its own but never to *apply* one. A human approves
it in Telegram or in the admin panel, and only then does the head act.

Keeping the proposal in the database rather than in the notification means
an approval that arrives an hour later still finds something to act on, a
head restart mid-update leaves a row that says so, and the admin panel and
the bot are looking at the same state instead of two private copies.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NodeUpdateStatus(str, enum.Enum):
    pending = "pending"  # detected, waiting for a human
    approved = "approved"  # a human said yes; the apply loop will pick it up
    applying = "applying"  # SSH session in progress
    applied = "applied"  # the node came back, see version_after
    declined = "declined"  # a human said no
    failed = "failed"  # the attempt did not finish; see error


# Statuses a row can still change from. Anything else is history.
OPEN_STATUSES = (
    NodeUpdateStatus.pending,
    NodeUpdateStatus.approved,
    NodeUpdateStatus.applying,
)


class NodeUpdate(Base):
    __tablename__ = "node_updates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Indexed: every read of this table is either "what is still open" or
    # "what does this node have on it", and both run on a schedule.
    node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("nodes.id", ondelete="CASCADE"), index=True
    )

    # The version the release feed offered when this proposal was raised.
    target_version: Mapped[str] = mapped_column(String(32))
    # What the node reported before the attempt, and after it. `version_after`
    # can legitimately be lower than `target_version`: the node's Xray comes
    # from the marzban-node image, which may not have caught up with the
    # upstream release yet. That is an outcome worth recording, not an error.
    version_before: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version_after: Mapped[str | None] = mapped_column(String(32), nullable=True)

    status: Mapped[NodeUpdateStatus] = mapped_column(
        Enum(NodeUpdateStatus, name="node_update_status"),
        default=NodeUpdateStatus.pending,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Set once the bot has actually told an operator about this row, so a
    # head restart between detection and delivery does not lose the message
    # and a slow bot does not cause the same update to be announced twice.
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set once the outcome has been reported back. Separate from
    # `notified_at` because an operator who approved an update needs to hear
    # how it went, and "we asked" and "we answered" are two different
    # deliveries that can each be lost independently.
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "telegram:<chat id>" or an admin panel username — enough to answer
    # "who authorised the restart that dropped everyone's connection".
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
