"""Who is allowed online right now, and for how long.

The service is funded entirely by advertising, so access is not a status
anyone holds — it is a stretch of time bought with attention. One completed
rewarded video buys one hour. When the hour is up the user watches another
or stops.

That makes this module the whole business model in one place, and it has to
answer three uncomfortable questions honestly:

**How do we know the ad was really watched?** We do not, from the client
alone. A modified APK can call "I watched it" as often as it likes. The
nonce flow below raises the cost — each grant needs a token the head issued
moments earlier, single-use and short-lived, so replaying one recorded call
forever does not work — but the only real answer is server-side
verification, where the ad network itself calls us. `redeem_verified` is
that path, and `AD_SSV_REQUIRED` is the switch that stops trusting the
client at all once it is wired up.

**What if no ad can be shown?** Fill rates are not 100%, and a network can
be down or unreachable. Gating access on it with no fallback means our
outage is total: a VPN that will not connect is not a degraded VPN. So a
failure to deliver grants a short grace period on the lower-priority class
(`Tier.grace`) — deliberately worse than the real thing, so it cannot
become the way to skip the ad. `access_grace_minutes = 0` turns it off for
anyone who would rather fail closed.

**What about the bot?** Telegram bots cannot show rewarded video; no such
SDK exists. So the bot cannot participate in this economy at all, and any
access it grants is a hole around the ads. It is therefore restricted to
explicitly allowed accounts and every grant is recorded with who made it.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.logs import AdView
from app.db.models.user import AdNonce, User
from app.services.timeutil import as_aware

logger = logging.getLogger(__name__)


class AccessError(RuntimeError):
    pass


class NoAccessError(AccessError):
    """The user has not bought any time and needs to watch an ad."""


class InvalidNonceError(AccessError):
    """The reward token is unknown, spent or expired."""


class GraceUnavailableError(AccessError):
    """The fallback is switched off, or was used too recently."""


@dataclass(frozen=True)
class AccessState:
    active: bool
    expires_at: datetime | None
    seconds_remaining: int
    # True when the current stretch came from the fallback rather than an
    # ad. The app says so, because a user on a degraded class deserves to
    # know why it feels slower.
    is_grace: bool


# --- reading -------------------------------------------------------------


def state_of(user: User) -> AccessState:
    expires = as_aware(user.access_expires_at)
    if expires is None:
        return AccessState(False, None, 0, False)

    remaining = (expires - datetime.now(UTC)).total_seconds()
    return AccessState(
        active=remaining > 0,
        expires_at=expires,
        seconds_remaining=max(0, int(remaining)),
        is_grace=bool(user.access_is_grace),
    )


def has_access(user: User) -> bool:
    return state_of(user).active


def require_access(user: User) -> None:
    if not has_access(user):
        raise NoAccessError("нужно посмотреть рекламу, чтобы подключиться")


# --- granting ------------------------------------------------------------


def _extend(user: User, minutes: int, *, is_grace: bool) -> AccessState:
    """Add time, from now or from the existing expiry, whichever is later.

    Stacking rather than replacing matters: someone who watches a second ad
    with ten minutes left should end up with seventy, not sixty. Replacing
    would quietly punish exactly the users the model depends on.
    """
    now = datetime.now(UTC)
    current = as_aware(user.access_expires_at)
    still_running = current is not None and current > now
    base = current if still_running else now

    ceiling = now + timedelta(hours=get_settings().access_max_hours)
    extended = base + timedelta(minutes=minutes)
    # Capped so nobody banks a month of access in one sitting, but never
    # below what they already had: watching an ad must not be able to
    # shorten access, which a plain min() would do if the ceiling were ever
    # lowered in configuration.
    user.access_expires_at = max(min(extended, ceiling), base)

    if not is_grace:
        # A real reward always puts the user on the full class.
        user.access_is_grace = False
    elif not (still_running and not user.access_is_grace):
        # Grace only marks them as such when they were not already running
        # on properly earned time.
        user.access_is_grace = True

    return state_of(user)


def issue_nonce(db: Session, user: User) -> AdNonce:
    """A single-use token the client returns when the ad finishes.

    Without it, one recorded HTTP call would be an unlimited access
    generator. With it, a forged grant needs a fresh token per hour, which
    is not security so much as friction — see `redeem_verified` for the
    real thing.
    """
    settings = get_settings()
    now = datetime.now(UTC)

    # One live token per user. Two would let a client bank them and redeem
    # a stack at once.
    for stale in db.scalars(
        select(AdNonce).where(AdNonce.user_id == user.id, AdNonce.redeemed_at.is_(None))
    ).all():
        stale.expires_at = now

    nonce = AdNonce(
        user_id=user.id,
        nonce=secrets.token_urlsafe(24),
        expires_at=now + timedelta(minutes=settings.ad_nonce_ttl_minutes),
    )
    db.add(nonce)
    db.flush()
    return nonce


def redeem_nonce(db: Session, user: User, nonce_value: str, *, source: str = "rewarded_video") -> AccessState:
    """Grant an hour against a token the client says it earned."""
    settings = get_settings()
    if settings.ad_ssv_required:
        # The network's own callback is authoritative; the client's word is
        # not accepted at all once that is configured.
        raise InvalidNonceError("сервер принимает только подтверждение от рекламной сети")

    now = datetime.now(UTC)
    nonce = db.scalar(select(AdNonce).where(AdNonce.nonce == nonce_value.strip()))
    if nonce is None or nonce.user_id != user.id:
        raise InvalidNonceError("неизвестный токен")
    if nonce.redeemed_at is not None:
        raise InvalidNonceError("токен уже использован")
    if as_aware(nonce.expires_at) <= now:
        raise InvalidNonceError("токен просрочен")

    nonce.redeemed_at = now
    return _record_view(db, user, settings.ad_reward_minutes, source=source, is_grace=False)


def redeem_verified(db: Session, nonce_value: str, *, source: str = "ssv") -> AccessState:
    """Grant against the ad network's server-to-server callback.

    Authoritative: the caller here is the network, not the device, so there
    is no client to lie. Wiring this up and setting `AD_SSV_REQUIRED` is
    what turns the nonce flow from friction into a real control.
    """
    now = datetime.now(UTC)
    nonce = db.scalar(select(AdNonce).where(AdNonce.nonce == nonce_value.strip()))
    if nonce is None:
        raise InvalidNonceError("неизвестный токен")
    if nonce.redeemed_at is not None:
        raise InvalidNonceError("токен уже использован")

    user = db.get(User, nonce.user_id)
    if user is None:
        raise InvalidNonceError("аккаунт больше не существует")

    nonce.redeemed_at = now
    return _record_view(db, user, get_settings().ad_reward_minutes, source=source, is_grace=False)


def grant_grace(db: Session, user: User) -> AccessState:
    """Let someone online when no ad could be delivered.

    Rate limited per user, because "the ad failed" is a claim the client
    makes about itself and would otherwise be the cheapest way to never see
    one. It also lands on the lower-priority class, so taking this path
    repeatedly is a worse experience than watching the video.
    """
    settings = get_settings()
    if settings.access_grace_minutes <= 0:
        raise GraceUnavailableError("запасной доступ отключён")

    now = datetime.now(UTC)
    last = as_aware(user.grace_granted_at)
    if last is not None and now - last < timedelta(hours=settings.access_grace_interval_hours):
        raise GraceUnavailableError("запасной доступ уже выдавался недавно")

    user.grace_granted_at = now
    logger.info("granting grace access to %s", user.id)
    return _record_view(db, user, settings.access_grace_minutes, source="grace", is_grace=True)


def grant_manual(db: Session, user: User, minutes: int, *, by: str) -> AccessState:
    """Access granted by a human — the admin panel, or the bot for testing.

    Recorded like any other grant precisely because it bypasses the ads: an
    unexplained gap between views and minutes served should be answerable.
    """
    logger.info("manual access grant of %d min to %s by %s", minutes, user.id, by)
    return _record_view(db, user, minutes, source=f"manual:{by}"[:32], is_grace=False)


def revoke(db: Session, user: User) -> None:
    user.access_expires_at = None
    user.access_is_grace = False
    db.flush()


def _record_view(db: Session, user: User, minutes: int, *, source: str, is_grace: bool) -> AccessState:
    db.add(AdView(user_id=user.id, reward_minutes=minutes, source=source))
    state = _extend(user, minutes, is_grace=is_grace)
    db.flush()
    return state
