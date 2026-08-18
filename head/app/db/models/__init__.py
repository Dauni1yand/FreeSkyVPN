"""Import every model module so they register on Base.metadata.

Alembic's initial revision (migrations/versions/0001_initial.py) and the
test suite both rely on this side effect — importing app.db.models is
enough to see the whole schema, without listing tables twice.
"""

from app.db.models.logs import (
    AdView,
    ConnectionLog,
    FailReport,
    NodeChannelEvent,
    TrafficUsage,
)
from app.db.models.node import Assignment, Inbound, Node, SniCandidate, SniProbe
from app.db.models.outbox import ConfigPush, PushReason
from app.db.models.plan import Payment, Plan, Subscription
from app.db.models.user import AuthIdentity, User, UserSession

__all__ = [
    "AdView",
    "Assignment",
    "AuthIdentity",
    "ConfigPush",
    "ConnectionLog",
    "FailReport",
    "Inbound",
    "Node",
    "NodeChannelEvent",
    "Payment",
    "Plan",
    "PushReason",
    "SniCandidate",
    "SniProbe",
    "Subscription",
    "TrafficUsage",
    "User",
    "UserSession",
]
