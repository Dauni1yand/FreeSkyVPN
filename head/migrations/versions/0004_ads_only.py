"""access bought with rewarded ads; subscriptions and plans removed

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-18

The service is funded entirely by advertising now. Access stops being a
status somebody holds and becomes a stretch of time paid for with attention,
so it moves onto the user row and the billing tables go.

The inbound_tier enum is renamed rather than replaced: the two `tc` priority
classes on every node are unchanged and only the vocabulary moves, so this
must not require re-provisioning the fleet.

Dropping plans/subscriptions/payments destroys data. That is the intent —
there is nothing to bill for any more — but it is worth knowing before
running it against a database that has real rows in them.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # --- access on the user row ---
    op.add_column("users", sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users",
        sa.Column("access_is_grace", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("users", sa.Column("grace_granted_at", sa.DateTime(timezone=True), nullable=True))
    # Nobody has a trial any more; there is nothing to have been trialled.
    op.drop_column("users", "trial_used_at")

    op.create_table(
        "ad_nonces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
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
    op.create_index("ix_ad_nonces_nonce", "ad_nonces", ["nonce"], unique=True)

    # --- the two service classes keep their ports and their tc rules ---
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE inbound_tier RENAME VALUE 'paid' TO 'full'")
        op.execute("ALTER TYPE inbound_tier RENAME VALUE 'free' TO 'grace'")
    else:
        # SQLite stores enums as plain strings, so the rows carry the old
        # words and have to be rewritten — and the column was declared
        # VARCHAR(4) to fit 'free'/'paid', which 'grace' does not.
        op.execute("UPDATE inbounds SET tier = 'full' WHERE tier = 'paid'")
        op.execute("UPDATE inbounds SET tier = 'grace' WHERE tier = 'free'")
        with op.batch_alter_table("inbounds") as batch:
            batch.alter_column(
                "tier",
                existing_type=sa.String(length=4),
                type_=sa.Enum("grace", "full", name="inbound_tier"),
                existing_nullable=False,
            )

    # --- billing goes ---
    op.drop_table("payments")
    op.drop_table("subscriptions")
    op.drop_table("plans")
    if bind.dialect.name == "postgresql":
        sa.Enum(name="subscription_type").drop(bind, checkfirst=True)
        sa.Enum(name="payment_status").drop(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("max_devices", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=True),
        sa.Column(
            "type", sa.Enum("trial", "paid", name="subscription_type"), nullable=False
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("auto_renew", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=128), nullable=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "succeeded", "failed", name="payment_status"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE inbound_tier RENAME VALUE 'full' TO 'paid'")
        op.execute("ALTER TYPE inbound_tier RENAME VALUE 'grace' TO 'free'")
    else:
        op.execute("UPDATE inbounds SET tier = 'paid' WHERE tier = 'full'")
        op.execute("UPDATE inbounds SET tier = 'free' WHERE tier = 'grace'")
        with op.batch_alter_table("inbounds") as batch:
            batch.alter_column(
                "tier",
                existing_type=sa.Enum("grace", "full", name="inbound_tier"),
                type_=sa.Enum("free", "paid", name="inbound_tier"),
                existing_nullable=False,
            )

    op.drop_index("ix_ad_nonces_nonce", table_name="ad_nonces")
    op.drop_table("ad_nonces")
    op.add_column("users", sa.Column("trial_used_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_column("users", "grace_granted_at")
    op.drop_column("users", "access_is_grace")
    op.drop_column("users", "access_expires_at")
