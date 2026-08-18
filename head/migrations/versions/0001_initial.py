"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-18

Frozen, explicit DDL — deliberately not `Base.metadata.create_all()`.

An earlier version of this baseline did use create_all, which is a trap:
it builds whatever the models say *today*, so the very next revision found
its columns already present and the first `alembic upgrade head` on a fresh
database died on "duplicate column". Offline SQL generation never catches
it, because `--sql` emits statements without applying them.

So the schema below is a snapshot, not a reflection. Later revisions are
ordinary diffs on top of it, generated with
`alembic revision --autogenerate` against a database already at this
baseline.
"""
import uuid as uuid_module
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Starting SNI pool, used until automatic discovery fills it in
# (app/services/sni_discovery.py). These are a floor, not a curated list:
# widely published defaults are also the most fingerprinted.
SEED_SNIS = [
    "www.samsung.com",
    "www.nvidia.com",
    "www.asus.com",
    "www.lg.com",
    "www.amd.com",
    "swdist.apple.com",
    "software.download.prss.microsoft.com",
]

# Prices are placeholders — set real ones before taking payments. The shape
# is what matters: several durations so the discount for committing longer
# is visible in the bot without a code change.
SEED_PLANS = [
    {"code": "month", "name": "1 месяц", "duration_days": 30, "max_devices": 3, "price": 199},
    {"code": "quarter", "name": "3 месяца", "duration_days": 90, "max_devices": 3, "price": 499},
    {"code": "year", "name": "1 год", "duration_days": 365, "max_devices": 5, "price": 1599},
]


def upgrade() -> None:
    op.create_table('admin_audit',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('admin_username', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('target', sa.String(length=255), nullable=True),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('admin_users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.create_table('nodes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('host', sa.String(length=255), nullable=False),
    sa.Column('control_port', sa.Integer(), nullable=False),
    sa.Column('country', sa.String(length=64), nullable=False),
    sa.Column('status', sa.Enum('active', 'draining', name='node_status'), nullable=False),
    sa.Column('ssh_user', sa.String(length=64), nullable=False),
    sa.Column('ssh_port', sa.Integer(), nullable=False),
    sa.Column('ssh_password_enc', sa.Text(), nullable=True),
    sa.Column('ssh_private_key_enc', sa.Text(), nullable=True),
    sa.Column('ssh_password_rotated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('uplink_mbit', sa.Integer(), nullable=True),
    sa.Column('capacity', sa.Integer(), nullable=False),
    sa.Column('tls_cert_pem', sa.Text(), nullable=True),
    sa.Column('channel_state', sa.Enum('active', 'degraded', 'isolated', name='node_channel_state'), nullable=False),
    sa.Column('consecutive_primary_fails', sa.Integer(), nullable=False),
    sa.Column('consecutive_fallback_fails', sa.Integer(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_channel_change_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('plans',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('duration_days', sa.Integer(), nullable=False),
    sa.Column('max_devices', sa.Integer(), nullable=False),
    sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=8), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('sni_candidates',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('domain', sa.String(length=255), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('burn_count', sa.Integer(), nullable=False),
    sa.Column('last_burned_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('domain')
    )
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('status', sa.Enum('active', 'banned', name='user_status'), nullable=False),
    sa.Column('trial_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('ad_views',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('watched_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('reward_minutes', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('auth_identities',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('provider', sa.Enum('telegram', 'phone', 'email', name='auth_provider'), nullable=False),
    sa.Column('provider_uid', sa.String(length=255), nullable=False),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'provider_uid', name='uq_auth_identity_provider_uid')
    )
    op.create_table('connection_logs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('node_id', sa.Uuid(), nullable=False),
    sa.Column('connected_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('disconnected_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['node_id'], ['nodes.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('inbounds',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('node_id', sa.Uuid(), nullable=False),
    sa.Column('port', sa.Integer(), nullable=False),
    sa.Column('sni', sa.String(length=255), nullable=False),
    sa.Column('transport', sa.String(length=32), nullable=False),
    sa.Column('tier', sa.Enum('free', 'paid', name='inbound_tier'), nullable=False),
    sa.Column('reality_private_key', sa.String(length=255), nullable=False),
    sa.Column('reality_public_key', sa.String(length=255), nullable=False),
    sa.Column('reality_short_id', sa.String(length=32), nullable=False),
    sa.Column('state', sa.Enum('active', 'suspect', 'dead', name='inbound_state'), nullable=False),
    sa.Column('fail_count', sa.Integer(), nullable=False),
    sa.Column('fail_window_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('died_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('is_control_channel', sa.Boolean(), nullable=False),
    sa.Column('control_client_uuid', sa.String(length=36), nullable=True),
    sa.ForeignKeyConstraint(['node_id'], ['nodes.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('node_channel_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('node_id', sa.Uuid(), nullable=False),
    sa.Column('from_state', sa.String(length=16), nullable=False),
    sa.Column('to_state', sa.String(length=16), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['node_id'], ['nodes.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('payments',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=False),
    sa.Column('amount', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=8), nullable=False),
    sa.Column('status', sa.Enum('pending', 'succeeded', 'failed', name='payment_status'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'external_id', name='uq_payment_provider_external_id')
    )
    op.create_table('sessions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('client_type', sa.Enum('bot', 'android', name='client_type'), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('sni_probes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('node_id', sa.Uuid(), nullable=False),
    sa.Column('candidate_id', sa.Uuid(), nullable=False),
    sa.Column('ok', sa.Boolean(), nullable=False),
    sa.Column('tls_version', sa.String(length=16), nullable=True),
    sa.Column('alpn', sa.String(length=16), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('from_node', sa.Boolean(), nullable=False),
    sa.Column('checked_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['sni_candidates.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['node_id'], ['nodes.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('node_id', 'candidate_id', name='uq_sni_probe_node_candidate')
    )
    op.create_table('subscriptions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('plan_id', sa.Uuid(), nullable=True),
    sa.Column('type', sa.Enum('trial', 'paid', name='subscription_type'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('auto_renew', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('traffic_usage',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('node_id', sa.Uuid(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('bytes_up', sa.BigInteger(), nullable=False),
    sa.Column('bytes_down', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['node_id'], ['nodes.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('assignments',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('inbound_id', sa.Uuid(), nullable=False),
    sa.Column('xray_uuid', sa.String(length=36), nullable=False),
    sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['inbound_id'], ['inbounds.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('fail_reports',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('inbound_id', sa.Uuid(), nullable=False),
    sa.Column('reported_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['inbound_id'], ['inbounds.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('config_pushes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('assignment_id', sa.Uuid(), nullable=False),
    sa.Column('reason', sa.Enum('inbound_blocked', 'node_burned', 'tier_changed', name='push_reason'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('delivery_error', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['assignment_id'], ['assignments.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


    _seed()


def _seed() -> None:
    """Enough data for a fresh install to be usable immediately.

    Without an SNI candidate the config selector cannot mint an inbound, and
    without a plan the bot has nothing to sell — both would look like bugs on
    a first deploy rather than an empty database.
    """
    sni_candidates = sa.table(
        "sni_candidates",
        sa.column("id", sa.Uuid),
        sa.column("domain", sa.String),
        sa.column("active", sa.Boolean),
        sa.column("burn_count", sa.Integer),
        sa.column("source", sa.String),
    )
    op.bulk_insert(
        sni_candidates,
        [
            {
                "id": uuid_module.uuid4(),
                "domain": domain,
                "active": True,
                "burn_count": 0,
                "source": "static",
            }
            for domain in SEED_SNIS
        ],
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
        [
            {**plan, "id": uuid_module.uuid4(), "currency": "RUB", "active": True}
            for plan in SEED_PLANS
        ],
    )


def downgrade() -> None:
    op.drop_table('config_pushes')
    op.drop_table('fail_reports')
    op.drop_table('assignments')
    op.drop_table('traffic_usage')
    op.drop_table('subscriptions')
    op.drop_table('sni_probes')
    op.drop_table('sessions')
    op.drop_table('payments')
    op.drop_table('node_channel_events')
    op.drop_table('inbounds')
    op.drop_table('connection_logs')
    op.drop_table('auth_identities')
    op.drop_table('ad_views')
    op.drop_table('users')
    op.drop_table('sni_candidates')
    op.drop_table('plans')
    op.drop_table('nodes')
    op.drop_table('admin_users')
    op.drop_table('admin_audit')
