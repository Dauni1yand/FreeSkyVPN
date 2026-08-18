"""Encryption for secrets that must be stored and later used, not just verified.

Node SSH credentials and Reality private keys fall in this category: the
head has to present them again, so they cannot be hashed. Encrypting them
means a database dump alone — a backup file, a stolen replica, an SQL
injection — does not hand over the fleet, because the key lives in the
process environment rather than in the database.

Fernet (AES-128-CBC + HMAC) is the right weight here: authenticated, hard
to misuse, and part of `cryptography`, which is already a dependency.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class SecretsNotConfiguredError(RuntimeError):
    """No encryption key is set, so secrets cannot be stored safely."""


def _fernet() -> Fernet:
    key = get_settings().secrets_key
    if not key or key == "change-me":
        raise SecretsNotConfiguredError(
            "SECRETS_KEY is unset or left at its default; refusing to store credentials in the clear"
        )
    # Accept any passphrase rather than demanding a base64 Fernet key: an
    # operator pasting a long random string should not have to know Fernet's
    # key format for it to be handled correctly.
    digest = hashlib.sha256(key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise SecretsNotConfiguredError(
            "stored secret could not be decrypted — SECRETS_KEY has changed since it was written"
        ) from exc


def is_configured() -> bool:
    """Whether secrets can be stored at all. Surfaced in the admin UI so a
    misconfigured deployment is visible before someone types a password."""
    key = get_settings().secrets_key
    return bool(key) and key != "change-me"
