"""payment idempotency key and seed plans

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-18
"""
import uuid as uuid_module
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Prices are placeholders — set real ones before going live. Structure is what
# matters here: several durations at one device count, so the discount for
# committing longer is visible in the bot without any code change.
SEED_PLANS = [
    {"code": "month", "name": "1 месяц", "duration_days": 30, "max_devices": 3, "price": 199},
    {"code": "quarter", "name": "3 месяца", "duration_days": 90, "max_devices": 3, "price": 499},
    {"code": "year", "name": "1 год", "duration_days": 365, "max_devices": 5, "price": 1599},
]


def upgrade() -> None:
    # Existing rows (there are none in practice at this stage) need a value
    # before the column can be NOT NULL.
    op.add_column("payments", sa.Column("external_id", sa.String(length=255), nullable=True))
    op.execute("UPDATE payments SET external_id = id::text WHERE external_id IS NULL")
    op.alter_column("payments", "external_id", nullable=False)
    op.create_unique_constraint(
        "uq_payment_provider_external_id", "payments", ["provider", "external_id"]
    )

    plans = sa.table(
        "plans",
        sa.column("id", sa.Uuid),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("duration_days", sa.Integer),
        sa.column("max_devices", sa.Integer),
        sa.column("price", sa.Numeric),
        sa.column("currency", sa.String),
        sa.column("active", sa.Boolean),
    )
    op.bulk_insert(
        plans,
        [{**plan, "id": uuid_module.uuid4(), "currency": "RUB", "active": True} for plan in SEED_PLANS],
    )


def downgrade() -> None:
    op.execute("DELETE FROM plans WHERE code IN ('month', 'quarter', 'year')")
    op.drop_constraint("uq_payment_provider_external_id", "payments", type_="unique")
    op.drop_column("payments", "external_id")
