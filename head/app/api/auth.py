"""Service-to-service authentication for the head API.

Every endpoint here is currently called by trusted server-side clients (the
Telegram bot, provisioning scripts) rather than by end users, so a single
shared secret is the right weight of mechanism. Without it `/connect` would
hand out a working config for any `user_id` a caller cared to name, and
`/admin/grant-access` would let anyone put themselves online for free.

The Android phase adds per-user tokens on top; those authenticate the
*user*, while this keeps authenticating the *caller*.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config import get_settings


def require_service_token(x_service_token: Annotated[str | None, Header()] = None) -> None:
    expected = get_settings().head_secret_key
    if not expected or expected == "change-me":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="HEAD_SECRET_KEY is unset or left at its default; refusing to serve",
        )
    # constant-time compare so the token cannot be recovered by timing
    if x_service_token is None or not secrets.compare_digest(x_service_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid service token")


ServiceAuth = Depends(require_service_token)
