from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://freeskyvpn:freeskyvpn@localhost:5432/freeskyvpn"
    head_secret_key: str = "change-me"

    telegram_bot_token: str = ""
    telegram_admin_chat_id: str = ""

    # control-channel resilience thresholds (see app/node_manager/channel.py)
    node_channel_primary_timeout_s: float = 3.0
    node_channel_primary_fails_before_fallback: int = 3
    node_channel_fallback_fails_before_isolated: int = 3

    xray_client_binary_path: str = "/usr/local/bin/xray"


@lru_cache
def get_settings() -> Settings:
    return Settings()
