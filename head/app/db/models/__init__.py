"""Import every model module so they register on Base.metadata.

Alembic's initial revision (migrations/versions/0001_initial.py) and the
test suite both rely on this side effect — importing app.db.models is
enough to see the whole schema, without listing tables twice.
"""

from app.db.models.admin import AdminAudit, AdminUser
from app.db.models.logs import (
    AdView,
    ConnectionLog,
    FailReport,
    NodeChannelEvent,
    TrafficUsage,
)
from app.db.models.node import Assignment, Inbound, Node, SniCandidate, SniProbe
from app.db.models.outbox import ConfigPush, PushReason
from app.db.models.update import NodeUpdate, NodeUpdateStatus
from app.db.models.user import AdNonce, AuthIdentity, LinkCode, User, UserSession

__all__ = [
    "AdNonce",
    "AdView",
    "AdminAudit",
    "AdminUser",
    "Assignment",
    "AuthIdentity",
    "ConfigPush",
    "ConnectionLog",
    "FailReport",
    "Inbound",
    "LinkCode",
    "Node",
    "NodeChannelEvent",
    "NodeUpdate",
    "NodeUpdateStatus",
    "PushReason",
    "SniCandidate",
    "SniProbe",
    "TrafficUsage",
    "User",
    "UserSession",
]
