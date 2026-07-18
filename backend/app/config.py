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

    vapid_public_key: str = Field("", alias="VAPID_PUBLIC_KEY")
    vapid_private_key: str = Field("", alias="VAPID_PRIVATE_KEY")
    vapid_subject: str = Field("mailto:you@example.com", alias="VAPID_SUBJECT")

    # "webpush" (real Web Push) or "console" (log payloads — for local dev/demo
    # without a browser subscription).
    push_transport: str = Field("webpush", alias="PUSH_TRANSPORT")

    api_port: int = Field(8000, alias="API_PORT")
    poll_interval_seconds: int = Field(20, alias="POLL_INTERVAL_SECONDS")
    cors_origin: str = Field("http://localhost:5173", alias="CORS_ORIGIN")

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
