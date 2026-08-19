"""Operator commands that must work before anyone can log in.

    python -m app.cli create-admin <username> [password]
    python -m app.cli generate-key
    python -m app.cli egress-url

`create-admin` is the bootstrap: the panel has no sign-up, so the first
operator has to be made from a shell on the head. It also resets an
existing operator's password, which is the recovery path when someone is
locked out.
"""

from __future__ import annotations

import secrets
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models.user import AuthIdentity, AuthProvider, User
from app.db.session import SessionLocal
from app.services.admin_auth import ensure_admin
from app.services.config_selector import NoCapacityError, assign_config


def create_admin(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m app.cli create-admin <username> [password]", file=sys.stderr)
        return 2

    username = argv[0]
    password = argv[1] if len(argv) > 1 else secrets.token_urlsafe(18)
    generated = len(argv) < 2

    with SessionLocal() as db:
        ensure_admin(db, username, password)
        db.commit()

    print(f"admin '{username}' ready")
    if generated:
        print(f"password: {password}")
        print("Store it now — it is not recoverable, only resettable by re-running this command.")
    return 0


def generate_key(_argv: list[str]) -> int:
    """A key for SECRETS_KEY / HEAD_SECRET_KEY."""
    print(secrets.token_urlsafe(48))
    return 0


# The account the egress proxy connects as. A normal user row on purpose:
# it lands on an ordinary inbound alongside real customers, so its traffic
# looks like theirs rather than like a control channel worth blocking.
EGRESS_UID = "system:egress"


def egress_url(_argv: list[str]) -> int:
    """Print a vless:// link for the egress container.

    Telegram is blocked where the head runs, so the bot needs a way out.
    This hands it the same kind of config a customer gets, on your own
    fleet — see provisioning/egress.py and the compose service that uses it.
    """
    with SessionLocal() as db:
        identity = db.scalar(
            select(AuthIdentity).where(
                AuthIdentity.provider == AuthProvider.device,
                AuthIdentity.provider_uid == EGRESS_UID,
            )
        )
        if identity is None:
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
        else:
            user = identity.user

        # Long-lived rather than bought with an ad: this account exists so
        # that alerts can be delivered, and an alert path that expires is
        # an alert path that is silent when it matters.
        user.access_expires_at = datetime.now(UTC) + timedelta(days=3650)
        user.access_is_grace = False

        try:
            config = assign_config(db, user)
        except NoCapacityError as exc:
            print(f"нет свободной ноды: {exc}", file=sys.stderr)
            print("Добавьте ноду в админке и повторите.", file=sys.stderr)
            return 1
        db.commit()

    print(config.vless_url)
    print(f"\nНода: {config.node_country}", file=sys.stderr)
    print("Впишите строку выше в .env как EGRESS_VLESS_URL, затем:", file=sys.stderr)
    print("  docker compose up -d", file=sys.stderr)
    return 0


COMMANDS = {
    "create-admin": create_admin,
    "generate-key": generate_key,
    "egress-url": egress_url,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"usage: python -m app.cli {{{'|'.join(COMMANDS)}}} [args]", file=sys.stderr)
        return 2
    return COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
