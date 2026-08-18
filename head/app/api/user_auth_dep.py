"""FastAPI dependency that turns a bearer token into a User.

Separate from app/api/auth.py on purpose: that module answers "is this
caller part of our system", this one answers "and which user is it acting
for". The Android app presents both — the service token identifies the
client build, the bearer token identifies the person — and endpoints under
/api/v1/me require the second.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.api.deps import DbSession
from app.db.models.user import User
from app.services import user_auth


def current_user(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[7:].strip()

    try:
        return user_auth.authenticate(db, token)
    except user_auth.BannedError as exc:
        # 403 rather than 401: the token is valid and re-authenticating will
        # not help, so a client that retries on 401 must not loop here.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except user_auth.AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentUser = Annotated[User, Depends(current_user)]
