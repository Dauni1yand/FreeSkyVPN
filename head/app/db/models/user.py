import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserStatus(str, enum.Enum):
    active = "active"
    banned = "banned"


class AuthProvider(str, enum.Enum):
    telegram = "telegram"
    phone = "phone"
    email = "email"


class ClientType(str, enum.Enum):
    bot = "bot"
    android = "android"


class User(Base):
    """The one and only identity. Telegram/phone/email are just ways in — see AuthIdentity."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus, name="user_status"), default=UserStatus.active)
    trial_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    auth_identities: Mapped[list["AuthIdentity"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AuthIdentity(Base):
    """One row per way a user can log in. A user can hold several (telegram + phone, etc.)."""

    __tablename__ = "auth_identities"
    __table_args__ = (UniqueConstraint("provider", "provider_uid", name="uq_auth_identity_provider_uid"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[AuthProvider] = mapped_column(Enum(AuthProvider, name="auth_provider"))
    provider_uid: Mapped[str] = mapped_column(String(255))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="auth_identities")


class UserSession(Base):
    """An issued client session/token. Counted against plans.max_devices."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    client_type: Mapped[ClientType] = mapped_column(Enum(ClientType, name="client_type"))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")
