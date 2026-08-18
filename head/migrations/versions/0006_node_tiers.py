"""free/paid node tiers

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-18

Existing nodes default to the free tier: assuming an unshaped node is a paid
one would silently promise paying users capacity that was never set aside
for them. An operator marks the paid nodes explicitly.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    node_tier = sa.Enum("free", "paid", name="node_tier")
    node_tier.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "nodes",
        sa.Column("tier", node_tier, nullable=False, server_default="free"),
    )
    op.add_column("nodes", sa.Column("shaped_mbit", sa.Integer(), nullable=True))
    # selection filters by tier on every connect
    op.create_index("ix_nodes_tier_status", "nodes", ["tier", "status"])

    op.execute("ALTER TYPE push_reason ADD VALUE IF NOT EXISTS 'tier_changed'")


def downgrade() -> None:
    op.drop_index("ix_nodes_tier_status", table_name="nodes")
    op.drop_column("nodes", "shaped_mbit")
    op.drop_column("nodes", "tier")
    sa.Enum(name="node_tier").drop(op.get_bind(), checkfirst=True)
    # postgres cannot remove a value from an enum; 'tier_changed' stays.
