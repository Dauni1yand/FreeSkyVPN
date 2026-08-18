"""node_updates: proposed Xray version updates awaiting an operator's word

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-18

An ordinary diff on top of the frozen 0001 baseline. One new table and no
changes to existing ones, so an upgrade on a running deployment does not
touch a single row anyone is currently using.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "node_updates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("target_version", sa.String(length=32), nullable=False),
        sa.Column("version_before", sa.String(length=32), nullable=True),
        sa.Column("version_after", sa.String(length=32), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "applying",
                "applied",
                "declined",
                "failed",
                name="node_update_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_node_updates_status", "node_updates", ["status"])
    op.create_index("ix_node_updates_node_id", "node_updates", ["node_id"])


def downgrade() -> None:
    op.drop_index("ix_node_updates_node_id", table_name="node_updates")
    op.drop_index("ix_node_updates_status", table_name="node_updates")
    op.drop_table("node_updates")
    # Postgres keeps the enum type after the table goes; SQLite has no such
    # object and errors on the DROP, so this is conditional on the dialect.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name="node_update_status").drop(bind, checkfirst=True)
