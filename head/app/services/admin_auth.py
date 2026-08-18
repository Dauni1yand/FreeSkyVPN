"""Admin login: password hashing, session cookies, audit.

Uses `hashlib.scrypt` from the standard library rather than pulling in
passlib/bcrypt. scrypt is memory-hard, ships with Python, and needs no
compiled dependency on the head — one less thing to get wrong at deploy
time for no loss in strength at these parameters.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.admin import AdminAudit, AdminUser

logger = logging.getLogger(__name__)

SESSION_COOKIE = "freesky_admin"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, expected_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived.hex(), expected_hex)


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().head_secret_key, salt="freesky-admin-session")


def issue_session(username: str) -> str:
    return _serializer().dumps({"u": username})


def read_session(token: str) -> str | None:
    max_age = get_settings().admin_session_hours * 3600
    try:
        data = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("u")


def authenticate(db: Session, username: str, password: str) -> AdminUser | None:
    admin = db.scalar(select(AdminUser).where(AdminUser.username == username))
    # Hash even when the user does not exist, so a missing username and a
    # wrong password take the same time and cannot be told apart.
    stored = admin.password_hash if admin else hash_password("placeholder")
    if not verify_password(password, stored) or admin is None or not admin.active:
        return None

    admin.last_login_at = datetime.now(UTC)
    db.flush()
    return admin


def audit(db: Session, username: str, action: str, target: str | None = None, detail: str | None = None) -> None:
    db.add(AdminAudit(admin_username=username, action=action, target=target, detail=detail))
    db.flush()
    logger.info("admin %s: %s %s", username, action, target or "")


def ensure_admin(db: Session, username: str, password: str) -> AdminUser:
    """Create or update an operator. Used by the bootstrap CLI."""
    admin = db.scalar(select(AdminUser).where(AdminUser.username == username))
    if admin is None:
        admin = AdminUser(username=username, password_hash=hash_password(password))
        db.add(admin)
    else:
        admin.password_hash = hash_password(password)
        admin.active = True
    db.flush()
    return admin
