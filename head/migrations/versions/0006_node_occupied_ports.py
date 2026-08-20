"""record ports already taken on a node

Ports the head picks for inbounds come from a short list of ordinary HTTPS
ports. Nothing checked whether the node already had something on them, so a
hoster panel on 8443 produced an inbound Xray could not bind and a config
that connected to nothing.

NULL means never probed, which is deliberately distinct from an empty list:
nodes provisioned before this exists should keep the old behaviour rather
than be treated as having nothing taken.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("occupied_ports", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("nodes", "occupied_ports")
