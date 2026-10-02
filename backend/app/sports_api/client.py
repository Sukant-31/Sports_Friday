"""HTTP client for the sports data API with exponential backoff on 429/5xx and
a small circuit breaker so a sustained outage stops hammering the API."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings
from app.logging_conf import get_logger

log = get_logger("sports_api")

_MAX_RETRIES = 3
_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN_S = 30.0


class SportsApiError(Exception):
    def __init__(self, message: str, status_code: int = 0, *, errors: dict | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.errors = errors or {}


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
        self._fixtures_by_date: dict[str, dict[str, Any]] = {}
        self._fixtures_date_limit: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not settings.sports_api_key:
            raise SportsApiError("Live sports data is not configured: SPORTS_API_KEY is missing")
        if time.monotonic() < self._breaker_open_until:
            raise SportsApiError("Circuit breaker open", 503)

        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await self._client.get(path, params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise SportsApiError(f"Upstream {resp.status_code}", resp.status_code)
                resp.raise_for_status()
                data = resp.json()
                if data.get("errors"):
                    raise SportsApiError(f"Sports API rejected request: {data['errors']}",
                                         errors=data['errors'])
                self._consecutive_failures = 0
                return data
            except (SportsApiError, httpx.HTTPError) as exc:
                status_code = getattr(exc, "status_code", 0)
                retriable = status_code == 429 or status_code >= 500
                if not retriable or attempt == _MAX_RETRIES:
                    self._register_failure()
                    raise SportsApiError(str(exc), status_code,
                                         errors=getattr(exc, "errors", None)) from exc
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

    async def get_team(self, external_id: str) -> dict[str, Any]:
        return await self._request("/teams", {"id": external_id})

    async def get_live_fixture(self, external_id: str) -> dict[str, Any]:
        return await self._request("/fixtures", {"id": external_id})

    async def get_live_fixtures(self) -> dict[str, Any]:
        return await self._request("/fixtures", {"live": "all"})

    async def get_team_live_fixtures(self, team_external_id: str) -> dict[str, Any]:
        return await self._request("/fixtures", {"team": team_external_id, "live": "all"})

    async def get_team_fixtures(self, team_external_id: str, count: int) -> dict[str, Any]:
        """Nearest fixtures in the next seven UTC dates, without paid filters.

        Team + date/from/to requires a season, which can also be blocked on
        Free. The global date feed works without a season. Share each feed
        across followed teams for this discovery pass to limit API usage.
        """
        if count <= 0:
            return {"response": [], "results": 0}
        now = datetime.now(timezone.utc)
        fixtures = {}
        for offset in range(7):
            day = (now.date() + timedelta(days=offset)).isoformat()
            if self._fixtures_date_limit is not None and day >= self._fixtures_date_limit:
                break
            if day not in self._fixtures_by_date:
                try:
                    self._fixtures_by_date[day] = await self._request("/fixtures", {"date": day})
                except SportsApiError as exc:
                    # Free permits a short rolling window. Keep valid dates and
                    # stop at its boundary; never hide quota/auth/network errors.
                    plan = str(exc.errors.get("plan", ""))
                    if self._fixtures_by_date and "Free plans do not have access to this date" in plan:
                        self._fixtures_date_limit = day
                        break
                    raise
            for fixture in self._fixtures_by_date[day].get("response", []):
                teams = fixture.get("teams", {})
                if not any(str(teams.get(side, {}).get("id")) == str(team_external_id)
                           for side in ("home", "away")):
                    continue
                info = fixture["fixture"]
                kickoff = datetime.fromisoformat(info["date"].replace("Z", "+00:00"))
                if kickoff >= now:
                    fixtures[info["id"]] = fixture
        upcoming = sorted(fixtures.values(), key=lambda fx: fx["fixture"]["date"])[:count]
        return {"response": upcoming, "results": len(upcoming)}

    def begin_fixture_discovery(self) -> None:
        """Refresh date feeds each pass, including in the persistent poller."""
        self._fixtures_by_date.clear()
        self._fixtures_date_limit = None
