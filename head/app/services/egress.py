"""The account the head's own outbound proxy connects as.

Telegram is blocked where the head runs, so the bot reaches it through a
tunnel to one of our own nodes. That tunnel needs a config, and the
simplest honest way to get one is to be a user: the egress holds an
ordinary account, lands on an ordinary inbound next to real customers, and
is indistinguishable from them on the wire.

Being a user also means the failure handling already written for users
applies unchanged. When a node stops carrying the egress's traffic, the
egress reports it exactly as a phone would, and the head moves it to
another node by the same code path — no second migration mechanism, and no
special case that only gets exercised during an outage.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.user import AuthIdentity, AuthProvider, User

logger = logging.getLogger(__name__)

#: Identifies the account among auth identities. Not a login: nothing signs
#: in as the egress, the head resolves it by this marker.
EGRESS_UID = "system:egress"

#: Long enough to never be the reason alerting went quiet. Access exists to
#: gate advertising revenue; the egress is not a customer and buys nothing,
#: so metering it would only create a way for the alert path to expire
#: unnoticed.
EGRESS_ACCESS = timedelta(days=3650)


def get_or_create(db: Session) -> User:
    identity = db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.provider == AuthProvider.device,
            AuthIdentity.provider_uid == EGRESS_UID,
        )
    )
    if identity is not None:
        user = identity.user
    else:
        user = User()
        db.add(user)
        db.flush()
        db.add(
            AuthIdentity(
                user_id=user.id,
                provider=AuthProvider.device,
                provider_uid=EGRESS_UID,
                verified_at=datetime.now(UTC),
            )
        )
        db.flush()
        logger.info("created the egress account %s", user.id)

    # Refreshed on every lookup rather than once at creation: an account
    # that silently expired would take the bot down with it, and the
    # symptom — a bot that stops answering — points nowhere near here.
    user.access_expires_at = datetime.now(UTC) + EGRESS_ACCESS
    user.access_is_grace = False
    db.flush()
    return user
