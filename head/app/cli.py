"""Operator commands that must work before anyone can log in.

    python -m app.cli create-admin <username> [password]
    python -m app.cli generate-key

`create-admin` is the bootstrap: the panel has no sign-up, so the first
operator has to be made from a shell on the head. It also resets an
existing operator's password, which is the recovery path when someone is
locked out.
"""

from __future__ import annotations

import secrets
import sys

from app.db.session import SessionLocal
from app.services.admin_auth import ensure_admin


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


COMMANDS = {"create-admin": create_admin, "generate-key": generate_key}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"usage: python -m app.cli {{{'|'.join(COMMANDS)}}} [args]", file=sys.stderr)
        return 2
    return COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
