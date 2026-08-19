"""access packages: 15 minutes, an hour, or two

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-18

Access stops being one fixed hour and becomes a choice made at connect
time. The token issued before the ads now carries which package it is for,
so the reward stays a server-side decision — a client able to name its own
reward would name a large one.

Existing tokens are backfilled to the hour package, which is what they
were.
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
        "ad_nonces",
        sa.Column("package", sa.String(length=16), nullable=False, server_default="hour"),
    )
    op.add_column(
        "ad_nonces",
        sa.Column("views_required", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "ad_nonces",
        sa.Column("views_done", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("ad_nonces", "views_done")
    op.drop_column("ad_nonces", "views_required")
    op.drop_column("ad_nonces", "package")
