from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = ""

    # The one chat allowed to approve Xray updates, and where update
    # notifications are sent. Empty disables both — the admin panel still
    # works, so an unset value costs a channel, not a capability.
    telegram_admin_chat_id: str = ""

    head_api_url: str = "http://localhost:8000"
    # must match HEAD_SECRET_KEY on the head (see head/app/api/auth.py)
    head_service_token: str = ""

    # Chats allowed to get online through the bot, comma separated.
    #
    # The bot cannot show rewarded video — no such SDK exists for bots — so
    # it cannot take part in what pays for the servers, and unrestricted
    # access through it would be the free tier this service deliberately
    # does not have. The admin chat is always allowed on top of this.
    telegram_allowed_chat_ids: str = ""

    # how often the outbox worker drains pending config pushes
    outbox_poll_seconds: int = 15
    # how often the bot asks the head about Xray updates worth announcing
    updates_poll_seconds: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
