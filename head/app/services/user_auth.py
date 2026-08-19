"""Per-user authentication for the Android app.

The bot never needed this. It runs on our own server, so the service token
in app/api/auth.py — which authenticates the *caller* — was enough, and
`/connect` could simply take a `user_id` in the body. That stops working the
moment a client is an APK on someone's phone: a shared secret shipped inside
an installable file is not a secret, and with it anyone could pull any
user's config or burn nodes on their behalf.

So the app gets a token of its own, bound to one account and revocable on
its own. Both mechanisms stay: the service token still says "this caller is
part of our system", and this one says "and it is acting for this user".

Registration is anonymous by design (the product is one button — a
registration form before the first connection is friction that buys the
user nothing). The identifier is generated here rather than accepted from
the client, because an identifier the client chooses is one an attacker can
choose too. The cost of anonymity is that a lost phone is a lost account,
which is exactly what `LinkCode` and `redeem_link` exist to fix.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.user import (
    AuthIdentity,
    AuthProvider,
    ClientType,
    LinkCode,
    User,
    UserSession,
    UserStatus,
)
from app.services.timeutil import as_aware

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "fsv1_"
# Digits only: this is read off one screen and typed into another, often by
# someone holding the phone in one hand.
_CODE_ALPHABET = "0123456789"


class AuthError(RuntimeError):
    pass


class BannedError(AuthError):
    pass


class InvalidLinkCodeError(AuthError):
    pass


@dataclass(frozen=True)
class IssuedToken:
    # Real UUIDs rather than their string forms: every consumer feeds these
    # straight back into `db.get()`, and a `Uuid` column silently refuses a
    # string with an error that names neither the column nor the caller.
    user_id: uuid.UUID
    token: str
    session_id: uuid.UUID


def hash_token(token: str) -> str:
    """SHA-256, not scrypt — see the note on UserSession.token_hash."""
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token() -> str:
    # 32 bytes of entropy. The prefix is there so a leaked string is
    # recognisable as one of ours in a log or a bug report, which is what
    # makes it possible to revoke rather than shrug.
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def issue_session(
    db: Session, user: User, *, client_type: ClientType, device_label: str | None = None
) -> IssuedToken:
    """Mint a token for one install. The plaintext is returned exactly once."""
    token = _new_token()
    session = UserSession(
        user_id=user.id,
        client_type=client_type,
        token_hash=hash_token(token),
        device_label=device_label[:120] if device_label else None,
        last_seen_at=datetime.now(UTC),
    )
    db.add(session)
    db.flush()
    return IssuedToken(user_id=user.id, token=token, session_id=session.id)


def register_device(db: Session, *, device_label: str | None = None) -> IssuedToken:
    """Create a fresh anonymous account and a token for it."""
    user = User()
    db.add(user)
    db.flush()

    db.add(
        AuthIdentity(
            user_id=user.id,
            provider=AuthProvider.device,
            # Server-generated: a client-chosen id would be a client-guessable
            # one, and guessing it is guessing your way into an account.
            provider_uid=secrets.token_urlsafe(24),
            verified_at=datetime.now(UTC),
        )
    )
    db.flush()
    logger.info("registered anonymous device account %s", user.id)
    return issue_session(db, user, client_type=ClientType.android, device_label=device_label)


def authenticate(db: Session, token: str) -> User:
    """Resolve a bearer token to its user, or raise.

    Looks the token up by hash rather than scanning sessions, so this stays a
    single indexed read no matter how many installs exist.
    """
    if not token:
        raise AuthError("no token")

    session = db.scalar(
        select(UserSession).where(
            UserSession.token_hash == hash_token(token),
            UserSession.revoked_at.is_(None),
        )
    )
    if session is None:
        raise AuthError("unknown or revoked token")

    user = db.get(User, session.user_id)
    if user is None:
        raise AuthError("token points at a deleted account")
    if user.status == UserStatus.banned:
        raise BannedError("account is banned")

    _touch(db, session)
    return user


def _touch(db: Session, session: UserSession) -> None:
    """Record liveness, but at most hourly.

    Without the throttle every authenticated read would become a write, and
    an app that polls its connection state would generate more database
    traffic than the whole rest of the service.
    """
    now = datetime.now(UTC)
    last = as_aware(session.last_seen_at)
    if last is None or now - last > timedelta(hours=1):
        session.last_seen_at = now
        db.flush()


def revoke_session(db: Session, session_id: uuid.UUID) -> bool:
    session = db.get(UserSession, session_id)
    if session is None or session.revoked_at is not None:
        return False
    session.revoked_at = datetime.now(UTC)
    db.flush()
    return True


# --- linking an anonymous account to Telegram ---------------------------


def start_link(db: Session, user: User) -> LinkCode:
    """Issue a code the user will type to the bot.

    Any code this user had outstanding is expired first, so the screen they
    are looking at is always the code that works — two live codes for one
    account is a support conversation waiting to happen.
    """
    settings = get_settings()
    now = datetime.now(UTC)

    for stale in db.scalars(
        select(LinkCode).where(LinkCode.user_id == user.id, LinkCode.redeemed_at.is_(None))
    ).all():
        stale.expires_at = now

    link = LinkCode(
        user_id=user.id,
        code=_unique_code(db, settings.link_code_length),
        expires_at=now + timedelta(minutes=settings.link_code_ttl_minutes),
    )
    db.add(link)
    db.flush()
    return link


def _unique_code(db: Session, length: int) -> str:
    for _ in range(20):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))
        if db.scalar(select(LinkCode).where(LinkCode.code == code)) is None:
            return code
    # Twenty collisions in a row means the space is effectively full. Failing
    # loudly beats handing out a code that belongs to somebody else.
    raise AuthError("could not allocate a link code")


def redeem_link(db: Session, code: str, telegram_id: str) -> User:
    """Attach `telegram_id` to the account that generated `code`.

    Called by the bot, which knows the Telegram id for certain because the
    message arrived from it.
    """
    now = datetime.now(UTC)
    link = db.scalar(select(LinkCode).where(LinkCode.code == code.strip()))
    if link is None or link.redeemed_at is not None:
        raise InvalidLinkCodeError("код неверен или уже использован")
    if as_aware(link.expires_at) <= now:
        raise InvalidLinkCodeError("код просрочен")

    app_user = db.get(User, link.user_id)
    if app_user is None:
        raise InvalidLinkCodeError("аккаунт больше не существует")

    existing = db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.provider == AuthProvider.telegram,
            AuthIdentity.provider_uid == telegram_id,
        )
    )

    if existing is None:
        # Nothing to merge: the Telegram side is new to us.
        db.add(
            AuthIdentity(
                user_id=app_user.id,
                provider=AuthProvider.telegram,
                provider_uid=telegram_id,
                verified_at=now,
            )
        )
        survivor = app_user
    elif existing.user_id == app_user.id:
        survivor = app_user  # already linked; redeeming again is harmless
    else:
        survivor = merge_accounts(db, keep=db.get(User, existing.user_id), absorb=app_user)

    link.redeemed_at = now
    db.flush()
    logger.info("linked telegram %s to account %s", telegram_id, survivor.id)
    return survivor


def merge_accounts(db: Session, *, keep: User, absorb: User) -> User:
    """Fold `absorb` into `keep`, then delete it.

    Which account survives is the caller's choice; *what* survives is decided
    here, and two rules matter:

    * Time bought with ads moves across, taking the later expiry. Losing an
      hour someone watched an ad for because they tapped "link account"
      would be the worst possible outcome of a convenience feature.
    * The grace cooldown takes the *later* of the two, so merging cannot
      hand back a fallback the absorbed account has just used.

    Assignments are deliberately *not* moved: a config belongs to the device
    holding it, both devices keep working, and the next placement sweep
    reconciles them against the merged entitlement.
    """
    if keep.id == absorb.id:
        return keep

    # Moved through the relationship, not by assigning `user_id`. Both
    # collections are `cascade="all, delete-orphan"`, and a child whose
    # foreign key was reassigned by hand is still sitting in the old
    # parent's loaded collection — so deleting `absorb` below would cascade
    # straight through it and take the row with it. That failure is
    # invisible until someone links their account and finds their app
    # signed out and their Telegram identity gone.
    for identity in list(absorb.auth_identities):
        keep.auth_identities.append(identity)
    for session in list(absorb.sessions):
        keep.sessions.append(session)

    # Link codes have no relationship on User, so nothing cascades to
    # them and reassigning the key is enough.
    for link in db.scalars(select(LinkCode).where(LinkCode.user_id == absorb.id)).all():
        link.user_id = keep.id

    # Access is time, so the merged account keeps whichever expiry is later
    # — the two stretches were bought separately and both were paid for.
    expiries = [t for t in (as_aware(keep.access_expires_at), as_aware(absorb.access_expires_at)) if t]
    if expiries:
        keep.access_expires_at = max(expiries)
        keep.access_is_grace = keep.access_is_grace and absorb.access_is_grace

    # The grace cooldown takes the *later* of the two: merging must not
    # hand back a fallback the absorbed account just used.
    graces = [t for t in (as_aware(keep.grace_granted_at), as_aware(absorb.grace_granted_at)) if t]
    keep.grace_granted_at = max(graces) if graces else None

    if absorb.status == UserStatus.banned:
        # A ban must not be shakeable by merging into a clean account.
        keep.status = UserStatus.banned

    db.flush()
    db.delete(absorb)
    db.flush()
    logger.info("merged account %s into %s", absorb.id, keep.id)
    return keep
