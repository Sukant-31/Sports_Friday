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


class SportsApiClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.sports_api_base_url,
            headers={
                # API-Football style; adjust header name for another provider.
                "x-apisports-key": settings.sports_api_key,
                "accept": "application/json",
            },
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
