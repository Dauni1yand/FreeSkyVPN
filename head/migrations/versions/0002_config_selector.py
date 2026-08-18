"""config selector: node tls cert, sni pool, config push outbox

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-18

Unlike revision 0001 (which created everything from Base.metadata as a
baseline), this and every later revision spell out their changes, so the
migration history stays a real diff of what changed.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# A starting pool only. These must be curated and rotated in production: a
# domain is usable while it speaks TLS 1.3 + HTTP/2, stays reachable from the
# target audience, and is not ours. The widely-published defaults are also the
# most fingerprinted, so treat this as a seed to replace, not a final list.
SEED_SNIS = [
    "www.samsung.com",
    "www.nvidia.com",
    "www.asus.com",
    "www.lg.com",
    "www.amd.com",
    "swdist.apple.com",
    "software.download.prss.microsoft.com",
]


def upgrade() -> None:
    op.add_column("nodes", sa.Column("tls_cert_pem", sa.Text(), nullable=True))

    sni_candidates = op.create_table(
        "sni_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("burn_count", sa.Integer(), nullable=False),
        sa.Column("last_burned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain"),
    )

    op.create_table(
        "config_pushes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column(
            "reason",
            sa.Enum("inbound_blocked", "node_burned", name="push_reason"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # the delivery layer (phase 3 bot) drains undelivered rows on every poll
    op.create_index(
        "ix_config_pushes_undelivered",
        "config_pushes",
        ["created_at"],
        postgresql_where=sa.text("delivered_at IS NULL"),
    )

    import uuid as uuid_module

    op.bulk_insert(
        sni_candidates,
        [
            {"id": uuid_module.uuid4(), "domain": domain, "active": True, "burn_count": 0}
            for domain in SEED_SNIS
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_config_pushes_undelivered", table_name="config_pushes")
    op.drop_table("config_pushes")
    sa.Enum(name="push_reason").drop(op.get_bind(), checkfirst=True)
    op.drop_table("sni_candidates")
    op.drop_column("nodes", "tls_cert_pem")
