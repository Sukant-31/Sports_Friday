"""Application configuration, loaded and validated from the environment.

Reads a .env file at the repo root when present. Missing required values fail
fast at import time so a service never boots half-configured.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = Field("development", alias="ENV")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    database_url: str = Field(..., alias="DATABASE_URL")
    redis_url: str = Field("redis://localhost:6379", alias="REDIS_URL")

    jwt_secret: str = Field(..., alias="JWT_SECRET")
    auth_cookie_name: str = Field("sports_token", alias="AUTH_COOKIE_NAME")

    sports_api_key: str = Field("", alias="SPORTS_API_KEY")
    sports_api_base_url: str = Field(
        "https://v3.football.api-sports.io", alias="SPORTS_API_BASE_URL"
    )
    # "apisports" (direct, dashboard.api-football.com) or "rapidapi".
    sports_api_provider: str = Field("apisports", alias="SPORTS_API_PROVIDER")

    vapid_public_key: str = Field("", alias="VAPID_PUBLIC_KEY")
    vapid_private_key: str = Field("", alias="VAPID_PRIVATE_KEY")
    vapid_subject: str = Field("mailto:you@example.com", alias="VAPID_SUBJECT")

    # "webpush" (real Web Push) or "console" (log payloads — for local dev/demo
    # without a browser subscription).
    push_transport: str = Field("webpush", alias="PUSH_TRANSPORT")

    api_port: int = Field(8000, alias="API_PORT")
    poll_interval_seconds: int = Field(20, alias="POLL_INTERVAL_SECONDS")
    # How often to (re)discover fixtures for subscribed teams. Much slower than
    # polling — the free API tier has a tight daily request budget.
    discover_interval_seconds: int = Field(3600, alias="DISCOVER_INTERVAL_SECONDS")
    # How many upcoming fixtures to pull per team on each discovery pass.
    fixtures_lookahead: int = Field(10, alias="FIXTURES_LOOKAHEAD")
    max_daily_api_requests: int = Field(90, ge=1, le=100, alias="MAX_DAILY_API_REQUESTS")
    api_priority_reserve: int = Field(15, ge=0, alias="API_PRIORITY_RESERVE")
    live_poll_interval_seconds: int = Field(180, ge=30, alias="LIVE_POLL_INTERVAL_SECONDS")
    pre_match_window_minutes: int = Field(15, ge=0, alias="PRE_MATCH_WINDOW_MINUTES")
    pre_match_poll_interval_seconds: int = Field(900, ge=30, alias="PRE_MATCH_POLL_INTERVAL_SECONDS")
    expected_match_minutes: int = Field(120, ge=90, alias="EXPECTED_MATCH_MINUTES")
    post_match_grace_minutes: int = Field(30, ge=0, alias="POST_MATCH_GRACE_MINUTES")
    status_recovery_hours: int = Field(6, ge=1, alias="STATUS_RECOVERY_HOURS")
    unresolved_poll_interval_seconds: int = Field(1800, ge=60, alias="UNRESOLVED_POLL_INTERVAL_SECONDS")
    fixture_discovery_days: int = Field(2, ge=1, le=7, alias="FIXTURE_DISCOVERY_DAYS")
    fixture_discovery_refresh_seconds: int = Field(21600, ge=300, alias="FIXTURE_DISCOVERY_REFRESH_SECONDS")
    cors_origin: str = Field("http://localhost:5173", alias="CORS_ORIGIN")
    # Shared secret Vercel Cron sends as "Authorization: Bearer <value>" so
    # /api/cron/poll can't be triggered by anyone else. Vercel sets this
    # automatically from the CRON_SECRET env var on Cron-triggered requests.
    cron_secret: str = Field("", alias="CRON_SECRET")

    @property
    def is_prod(self) -> bool:
        return self.env == "production"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origin.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
