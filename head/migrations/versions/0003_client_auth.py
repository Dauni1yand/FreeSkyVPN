"""per-user tokens for the Android client, and account linking

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-18

Adds token columns to `sessions` and a `link_codes` table. Existing rows in
`sessions` predate tokens and get NULL, which authenticates nobody — the bot
never used a session row, so nothing loses access here.

The `auth_provider` enum gains a `device` value. On PostgreSQL that needs an
explicit ALTER TYPE; SQLite stores enums as plain strings and needs nothing,
which is why this is branched on the dialect rather than written once.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        # Outside a transaction block in older servers; ADD VALUE IF NOT
        # EXISTS keeps a re-run from failing.
        op.execute("ALTER TYPE auth_provider ADD VALUE IF NOT EXISTS 'device'")

    op.add_column("sessions", sa.Column("token_hash", sa.String(length=64), nullable=True))
    op.add_column("sessions", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sessions", sa.Column("device_label", sa.String(length=120), nullable=True))
    # Every authenticated request is a lookup by this column.
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"])

    op.create_table(
        "link_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_link_codes_code", "link_codes", ["code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_link_codes_code", table_name="link_codes")
    op.drop_table("link_codes")
    op.drop_index("ix_sessions_token_hash", table_name="sessions")
    op.drop_column("sessions", "device_label")
    op.drop_column("sessions", "last_seen_at")
    op.drop_column("sessions", "token_hash")
    # The 'device' enum value is deliberately left in place: PostgreSQL has
    # no DROP VALUE, and recreating the type would mean rewriting every
    # column that uses it for no gain.
