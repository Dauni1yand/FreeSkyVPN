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
    # Ports are chosen per tier — see app/services/tiers.py, which must stay
    # in step with the tc filters installed on the node.
    #
    # How full a node may get before free users stop being admitted. The gap
    # between this and 1.0 is the headroom held for paying users, which is
    # what "paid goes first when load is high" means at admission time.
    free_admission_ratio: float = 0.8

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
    # Master switch for every in-process background loop.
    background_jobs_enabled: bool = True

    # --- admin panel ---
    # Encrypts node SSH credentials at rest (app/services/crypto.py).
    # Losing it makes stored credentials unrecoverable; changing it orphans them.
    secrets_key: str = "change-me"
    admin_session_hours: int = 12
    # Set false behind a reverse proxy that does not terminate TLS (dev only) —
    # a session cookie sent over plain HTTP can be lifted off the wire.
    admin_cookie_secure: bool = True
    # Where bootstrap_node.sh lives. Differs between a source checkout and the
    # container image, so it is configuration rather than a computed guess.
    bootstrap_script_path: str = ""

    # --- Xray updates (app/services/xray_updates.py) ---
    # How often the head asks the release feed and every node what they run.
    xray_update_check_interval_hours: int = 12
    # A release lookup is cached this long; the answer changes at most daily
    # and GitHub rate-limits unauthenticated callers.
    xray_release_cache_minutes: int = 60
    # How often approved updates are applied. Short, because this is the gap
    # between an operator tapping "обновить" and anything happening.
    xray_update_apply_interval_seconds: int = 60
    # Nodes updated per apply pass. Each one restarts that node's Xray, so
    # raising this takes more of the fleet down at once.
    xray_update_apply_batch: int = 1
    # After a failed attempt, wait this long before proposing the same
    # version again — otherwise a node that cannot take the update produces
    # a notification every check.
    xray_update_retry_hours: int = 24
    # Master switch for update detection. Leave on; approval is still manual.
    xray_update_check_enabled: bool = True

    # --- Android client (phase 5) ---
    # A code typed from one screen into a chat. Short because a human types
    # it; short-lived because six digits would otherwise be guessable.
    link_code_length: int = 6
    link_code_ttl_minutes: int = 10
    # Version of the split-tunnel policy served to apps. Bumping it makes
    # every client refetch (app/services/routing_policy.py).
    routing_policy_version: int = 1
    # Shown in the app's link screen ("напишите @<бот> код 123456"). Cosmetic;
    # empty just means the app names no bot.
    telegram_bot_username: str = ""

    # --- access, bought with attention (app/services/access.py) ---
    # What one completed rewarded video buys.
    ad_reward_minutes: int = 60
    # Ceiling on banked access, so nobody stacks a month in one sitting.
    access_max_hours: int = 24
    # How long the client has to finish an ad after asking for a token.
    ad_nonce_ttl_minutes: int = 15
    # Once the ad network's server-to-server callback is wired up, set this
    # and the head stops believing the client about what it watched.
    ad_ssv_required: bool = False

    # Fallback when no ad can be delivered. Fill rates are not 100% and a
    # network can be down; without this our outage is total, because a VPN
    # that will not connect is not a degraded VPN. Lands on the lower
    # priority class and is rate limited, so it cannot become the way to
    # skip the ad. Set to 0 to fail closed instead.
    access_grace_minutes: int = 15
    access_grace_interval_hours: int = 6

    # How often lapsed users are moved down to the grace class.
    tier_reconcile_interval_minutes: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
