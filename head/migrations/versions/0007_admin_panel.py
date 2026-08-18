"""admin panel: operators, audit trail, node SSH credentials

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-18
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )

    op.create_table(
        "admin_audit",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("admin_username", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_audit_at", "admin_audit", ["at"])

    # SSH access to nodes. The secrets are stored encrypted (see
    # app/services/crypto.py) — the head must present them again, so they
    # cannot be hashed, and a database dump should not hand over the fleet.
    op.add_column("nodes", sa.Column("ssh_user", sa.String(length=64), nullable=False, server_default="root"))
    op.add_column("nodes", sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="22"))
    op.add_column("nodes", sa.Column("ssh_password_enc", sa.Text(), nullable=True))
    op.add_column("nodes", sa.Column("ssh_private_key_enc", sa.Text(), nullable=True))
    op.add_column("nodes", sa.Column("ssh_password_rotated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("nodes", "ssh_password_rotated_at")
    op.drop_column("nodes", "ssh_private_key_enc")
    op.drop_column("nodes", "ssh_password_enc")
    op.drop_column("nodes", "ssh_port")
    op.drop_column("nodes", "ssh_user")
    op.drop_index("ix_admin_audit_at", table_name="admin_audit")
    op.drop_table("admin_audit")
    op.drop_table("admin_users")
