"""shared nodes: tier moves from node to inbound, nodes gain capacity

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-18

Replaces the free/paid *node* split with a shared-node model. Priority is
now expressed inside a node: each tier owns a set of ports, and `tc` on the
node serves the paid ports first when the link is contended.

Existing inbounds are marked free. That is the safe direction — a paying
user briefly on a free-tier inbound loses priority until the reconciliation
sweep moves them, whereas guessing the other way would hand free users
priority they never paid for. Nodes provisioned before this revision still
carry the old flat `tc` rules and must be re-provisioned to gain the two
priority classes; until then they simply serve everyone equally.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inbound_tier = sa.Enum("free", "paid", name="inbound_tier")
    inbound_tier.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "inbounds", sa.Column("tier", inbound_tier, nullable=False, server_default="free")
    )
    op.create_index("ix_inbounds_node_tier_state", "inbounds", ["node_id", "tier", "state"])

    op.add_column("nodes", sa.Column("uplink_mbit", sa.Integer(), nullable=True))
    op.add_column("nodes", sa.Column("capacity", sa.Integer(), nullable=False, server_default="200"))
    # Carry the old per-node shaping figure over as the node's link capacity
    # where one was recorded, so re-provisioning starts from a sane number.
    op.execute("UPDATE nodes SET uplink_mbit = shaped_mbit WHERE shaped_mbit IS NOT NULL")

    op.drop_index("ix_nodes_tier_status", table_name="nodes")
    op.drop_column("nodes", "shaped_mbit")
    op.drop_column("nodes", "tier")
    sa.Enum(name="node_tier").drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    node_tier = sa.Enum("free", "paid", name="node_tier")
    node_tier.create(op.get_bind(), checkfirst=True)
    op.add_column("nodes", sa.Column("tier", node_tier, nullable=False, server_default="free"))
    op.add_column("nodes", sa.Column("shaped_mbit", sa.Integer(), nullable=True))
    op.execute("UPDATE nodes SET shaped_mbit = uplink_mbit WHERE uplink_mbit IS NOT NULL")
    op.create_index("ix_nodes_tier_status", "nodes", ["tier", "status"])

    op.drop_column("nodes", "capacity")
    op.drop_column("nodes", "uplink_mbit")
    op.drop_index("ix_inbounds_node_tier_state", table_name="inbounds")
    op.drop_column("inbounds", "tier")
    sa.Enum(name="inbound_tier").drop(op.get_bind(), checkfirst=True)
