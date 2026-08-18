"""Identity endpoints — see blueprint §04.

Telegram, phone and email are all just ways into the same `User` row; this
router implements Scenario A (bot login) now and leaves phone/email/linking
(Scenarios B/C) for the Android phase, but the `auth_identities` table
already supports them without a schema change.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.api.auth import ServiceAuth
from app.api.deps import DbSession
from app.db.models.user import AuthIdentity, AuthProvider, User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"], dependencies=[ServiceAuth])


class TelegramLoginRequest(BaseModel):
    telegram_id: int


class UserResponse(BaseModel):
    user_id: uuid.UUID
    is_new: bool


@router.post("/telegram", response_model=UserResponse)
def login_with_telegram(payload: TelegramLoginRequest, db: DbSession) -> UserResponse:
    provider_uid = str(payload.telegram_id)

    identity = db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.provider == AuthProvider.telegram,
            AuthIdentity.provider_uid == provider_uid,
        )
    )
    if identity is not None:
        return UserResponse(user_id=identity.user_id, is_new=False)

    user = User()
    db.add(user)
    db.flush()  # populate user.id before it's referenced below

    db.add(AuthIdentity(user_id=user.id, provider=AuthProvider.telegram, provider_uid=provider_uid))
    db.commit()

    return UserResponse(user_id=user.id, is_new=True)
