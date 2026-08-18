"""automatic SNI discovery: candidate source and per-node probe verdicts

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-18

The hand-written seed list from 0002 stays in place as a floor, but is
relabelled `static` so it is distinguishable from what discovery pulls in.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sni_candidates",
        sa.Column("source", sa.String(length=32), nullable=False, server_default="static"),
    )

    op.create_table(
        "sni_probes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("tls_version", sa.String(length=16), nullable=True),
        sa.Column("alpn", sa.String(length=16), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("from_node", sa.Boolean(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["candidate_id"], ["sni_candidates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("node_id", "candidate_id", name="uq_sni_probe_node_candidate"),
    )
    # selection asks "which candidates are usable on this node", every time
    # an inbound is created
    op.create_index("ix_sni_probes_node_ok", "sni_probes", ["node_id", "ok"])


def downgrade() -> None:
    op.drop_index("ix_sni_probes_node_ok", table_name="sni_probes")
    op.drop_table("sni_probes")
    op.drop_column("sni_candidates", "source")
