"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-18

This revision creates the whole schema from Base.metadata instead of
hand-transcribed op.create_table() calls. That's a deliberate choice for
a revision-zero on a fresh project: the models in app/db/models are already
the single source of truth, and duplicating every column in DDL here would
just be a second place for the two to quietly drift apart.

From the *next* schema change onward, use
`alembic revision --autogenerate -m "..."` against a real dev database so
Alembic diffs against this baseline instead.
"""
from collections.abc import Sequence

from alembic import op

from app.db import models  # noqa: F401 - registers all tables on Base.metadata
from app.db.base import Base

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
