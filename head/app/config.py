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

    # head's own mTLS identity, presented to every node (see provisioning/generate_head_client_cert.sh)
    head_client_cert_path: str = "/etc/freeskyvpn/head_client_cert.pem"
    head_client_key_path: str = "/etc/freeskyvpn/head_client_key.pem"
    # each node's self-signed cert is stored in the DB and materialised here for httpx's verify=
    node_cert_cache_dir: str = "/var/lib/freeskyvpn/node_certs"

    # --- Config Selector (phase 2) ---
    # An inbound is considered blocked once this many distinct users report it
    # broken inside the window; below that, only the reporter is moved.
    inbound_fail_threshold: int = 5
    inbound_fail_window_minutes: int = 10
    # Per-user cooldown on the "не работает" button, so a user hammering it
    # cannot spin up inbound after inbound on our own nodes.
    fail_report_cooldown_seconds: int = 30
    # Once a node accumulates this many dead inbounds inside the fail window,
    # the node itself (not just its inbounds) is treated as burned and users
    # are migrated off it — a new port/SNI cannot fix a blocked IP.
    node_dead_inbound_threshold: int = 2
    # Ports tried first for new inbounds: all of them are ordinary HTTPS ports,
    # so a Reality listener on one looks unremarkable.
    preferred_ports: tuple[int, ...] = (443, 8443, 2053, 2083, 2087, 2096)
    fallback_port_range: tuple[int, int] = (20000, 60000)

    # --- automatic SNI discovery (app/services/sni_discovery.py) ---
    sni_use_tranco: bool = True
    # How many domains to keep in the candidate pool.
    sni_pool_size: int = 200
    # Skip the very top of the popularity ranking: the largest platforms are
    # the most likely to be individually handled by a censor, and several are
    # themselves blocked in the target market.
    sni_skip_top_ranks: int = 500
    sni_probe_batch: int = 100
    sni_probe_timeout_s: float = 6.0
    # A probe verdict older than this is treated as stale during selection.
    sni_probe_max_age_hours: int = 168
    # Fallback / supplementary domains when no popularity source is reachable.
    sni_seed_domains: tuple[str, ...] = ()
    # Never propose our own infrastructure as a destination.
    own_domains: tuple[str, ...] = ()
    sni_refresh_interval_hours: int = 24
    # Set false to run maintenance from cron (POST /api/v1/sni/refresh) instead
    # of in-process — useful when several head replicas would otherwise all probe.
    sni_maintenance_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
