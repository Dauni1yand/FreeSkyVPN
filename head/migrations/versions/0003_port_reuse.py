"""track when an inbound died, so its port can be recycled safely

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-18
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("inbounds", sa.Column("died_at", sa.DateTime(timezone=True), nullable=True))
    # Existing dead rows have no recorded death time. Backfilling from
    # fail_window_started_at is the closest available approximation — it was
    # set moments before the row was killed.
    op.execute(
        "UPDATE inbounds SET died_at = fail_window_started_at "
        "WHERE state = 'dead' AND died_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("inbounds", "died_at")
