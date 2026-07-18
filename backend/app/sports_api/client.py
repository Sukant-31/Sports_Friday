"""HTTP client for the sports data API with exponential backoff on 429/5xx and
a small circuit breaker so a sustained outage stops hammering the API."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.config import settings
from app.logging_conf import get_logger

log = get_logger("sports_api")

_MAX_RETRIES = 3
_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN_S = 30.0


class SportsApiError(Exception):
    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


_RAPIDAPI_BASE = "https://api-football-v1.p.rapidapi.com/v3"


def _base_and_headers() -> tuple[str, dict[str, str]]:
    """API-Football is reachable two ways with different auth:
      - direct (dashboard.api-football.com): x-apisports-key
      - via RapidAPI: x-rapidapi-key + x-rapidapi-host
    Select with SPORTS_API_PROVIDER = "apisports" (default) | "rapidapi".
    """
    key = settings.sports_api_key
    if settings.sports_api_provider == "rapidapi":
        base = settings.sports_api_base_url
        if "api-sports.io" in base:  # user left the direct default — override
            base = _RAPIDAPI_BASE
        host = base.split("//", 1)[-1].split("/", 1)[0]
        return base, {
            "x-rapidapi-key": key,
            "x-rapidapi-host": host,
            "accept": "application/json",
        }
    return settings.sports_api_base_url, {
        "x-apisports-key": key,
        "accept": "application/json",
    }


class SportsApiClient:
    def __init__(self) -> None:
        base_url, headers = _base_and_headers()
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=10.0,
        )
        self._consecutive_failures = 0
        self._breaker_open_until = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if time.monotonic() < self._breaker_open_until:
            raise SportsApiError("Circuit breaker open", 503)

        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await self._client.get(path, params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise SportsApiError(f"Upstream {resp.status_code}", resp.status_code)
                resp.raise_for_status()
                self._consecutive_failures = 0
                return resp.json()
            except (SportsApiError, httpx.HTTPError) as exc:
                status_code = getattr(exc, "status_code", 0)
                retriable = status_code == 429 or status_code >= 500
                if not retriable or attempt == _MAX_RETRIES:
                    self._register_failure()
                    raise SportsApiError(str(exc), status_code) from exc
                delay = 0.5 * (2**attempt)  # 0.5s, 1s, 2s
                log.debug("retrying %s after %.1fs (attempt %d)", path, delay, attempt)
                await asyncio.sleep(delay)
        raise SportsApiError("exhausted retries", 500)  # unreachable

    def _register_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _BREAKER_THRESHOLD:
            self._breaker_open_until = time.monotonic() + _BREAKER_COOLDOWN_S
            log.warning("sports API circuit breaker opened for %ss", _BREAKER_COOLDOWN_S)

    # --- endpoint wrappers ---
    async def search_teams(self, q: str) -> dict[str, Any]:
        return await self._request("/teams", {"search": q})

    async def get_live_fixture(self, external_id: str) -> dict[str, Any]:
        return await self._request("/fixtures", {"id": external_id})

    async def get_live_fixtures(self) -> dict[str, Any]:
        return await self._request("/fixtures", {"live": "all"})

    async def get_team_fixtures(self, team_external_id: str, count: int) -> dict[str, Any]:
        """Upcoming fixtures for a team (used by discovery to populate matches)."""
        return await self._request(
            "/fixtures", {"team": team_external_id, "next": count}
        )
