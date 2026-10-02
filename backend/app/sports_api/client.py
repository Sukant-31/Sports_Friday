"""HTTP client for the sports data API with exponential backoff on 429/5xx and
a small circuit breaker so a sustained outage stops hammering the API."""

from __future__ import annotations

import asyncio
import time
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings
from app.logging_conf import get_logger
from app.polling_schema import ensure_polling_schema
from app.repositories import sports_requests

log = get_logger("sports_api")

_MAX_RETRIES = 3
_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN_S = 30.0


class SportsApiError(Exception):
    def __init__(self, message: str, status_code: int = 0, *, errors: dict | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.errors = errors or {}


class ApiRequestSkipped(SportsApiError):
    def __init__(self, reason: str) -> None:
        super().__init__('API request skipped: ' + reason)
        self.reason = reason


class PartialFixtureDiscovery(SportsApiError):
    """Keep valid earlier date results while still reporting a later feed failure."""
    def __init__(self, error: SportsApiError, fixtures: dict, count: int):
        super().__init__('Fixture discovery partially failed', error.status_code, errors=error.errors)
        self.error = error
        self.fixtures = {'response': sorted(fixtures.values(),
                                           key=lambda fx: fx['fixture']['date'])[:count]}


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
        self._fixtures_date_errors: dict[str, SportsApiError] = {}
        self._fixtures_date_limit: str | None = None
        self.scope = settings.sports_api_provider + ':' + hashlib.sha256(
            settings.sports_api_key.encode()).hexdigest()[:16]
        self.stats = {'attempted': 0, 'made': 0, 'skipped': 0, 'cache_hits': 0, 'skip_reasons': {}}
        self._budget_ready = False

    async def aclose(self) -> None:
        await self._client.aclose()

    def _skip(self, reason: str) -> None:
        self.stats['skipped'] += 1
        reasons = self.stats['skip_reasons']
        reasons[reason] = reasons.get(reason, 0) + 1
        log.info('api_request_skipped reason=%s', reason)
        raise ApiRequestSkipped(reason)

    async def _request(self, path: str, params: dict[str, Any], *,
                       priority: str = 'low', cache_seconds: int = 0) -> dict[str, Any]:
        if not settings.sports_api_key:
            raise SportsApiError('Live sports data is not configured: SPORTS_API_KEY is missing')
        if not self._budget_ready:
            await ensure_polling_schema()
            self._budget_ready = True
        key = path + ':' + json.dumps(params, sort_keys=True)
        if cache_seconds:
            cached = await sports_requests.cached(self.scope, key, cache_seconds)
            if cached is not None:
                self.stats['cache_hits'] += 1
                return cached
            async with sports_requests.lock(self.scope, 'cache:' + key) as held:
                if not held:
                    self._skip('concurrent_request')
                cached = await sports_requests.cached(self.scope, key, cache_seconds)
                if cached is not None:
                    self.stats['cache_hits'] += 1
                    return cached
                result = await self._fetch(path, params, priority)
                await sports_requests.cache(self.scope, key, result)
                return result
        return await self._fetch(path, params, priority)

    async def _fetch(self, path: str, params: dict[str, Any], priority: str) -> dict[str, Any]:
        if time.monotonic() < self._breaker_open_until:
            raise SportsApiError('Circuit breaker open', 503)
        limit = settings.max_daily_api_requests
        if priority != 'high':
            limit = max(0, limit - settings.api_priority_reserve)
        last_failure = None
        for attempt in range(_MAX_RETRIES + 1):
            self.stats['attempted'] += 1
            if not await sports_requests.reserve(self.scope, limit):
                snapshot = await sports_requests.budget(self.scope, settings.max_daily_api_requests)
                try:
                    self._skip('budget_exhausted' if snapshot['remaining'] == 0 else 'priority_reserve')
                except ApiRequestSkipped:
                    if last_failure is not None:
                        raise SportsApiError('Upstream failure; retry stopped by budget',
                                             last_failure) from None
                    raise
            # Reservations are conservative: even a timed-out HTTP attempt may
            # have reached the provider. Never refund an ambiguous attempt.
            self.stats['made'] += 1
            try:
                resp = await self._client.get(path, params=params)
                remaining = resp.headers.get('x-ratelimit-requests-remaining')
                provider_limit = resp.headers.get('x-ratelimit-requests-limit')
                if remaining is not None and provider_limit is not None:
                    try:
                        used = max(0, int(provider_limit) - int(remaining))
                    except ValueError:
                        pass
                    else:
                        await sports_requests.reconcile(self.scope, used)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise SportsApiError(f'Upstream {resp.status_code}', resp.status_code)
                resp.raise_for_status()
                data = resp.json()
                if data.get('errors'):
                    raise SportsApiError('Sports API rejected request', errors=data['errors'])
                self._consecutive_failures = 0
                return data
            except (SportsApiError, httpx.HTTPError) as exc:
                status_code = getattr(exc, 'status_code', 0)
                if isinstance(exc, httpx.HTTPStatusError):
                    status_code = exc.response.status_code
                retriable = status_code == 429 or status_code >= 500
                last_failure = status_code
                if not retriable or attempt == _MAX_RETRIES:
                    self._register_failure()
                    raise SportsApiError('Sports API request failed', status_code,
                                         errors=getattr(exc, 'errors', None)) from exc
                await asyncio.sleep(0.5 * (2**attempt))
        raise SportsApiError('exhausted retries', 500)

    def _register_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _BREAKER_THRESHOLD:
            self._breaker_open_until = time.monotonic() + _BREAKER_COOLDOWN_S
            log.warning("sports API circuit breaker opened for %ss", _BREAKER_COOLDOWN_S)

    # --- endpoint wrappers ---
    async def search_teams(self, q: str) -> dict[str, Any]:
        return await self._request("/teams", {"search": q}, cache_seconds=900)

    async def get_team(self, external_id: str) -> dict[str, Any]:
        return await self._request("/teams", {"id": external_id}, cache_seconds=86400)

    async def get_live_fixture(self, external_id: str) -> dict[str, Any]:
        return await self._request("/fixtures", {"id": external_id}, priority="high")

    async def get_live_fixtures(self) -> dict[str, Any]:
        return await self._request("/fixtures", {"live": "all"}, priority="high",
                                   cache_seconds=settings.live_poll_interval_seconds)

    async def get_team_live_fixtures(self, team_external_id: str) -> dict[str, Any]:
        return await self._request("/fixtures", {"team": team_external_id, "live": "all"})

    async def get_team_fixtures(self, team_external_id: str, count: int) -> dict[str, Any]:
        """Today/current and upcoming fixtures in the configured UTC date window, without paid filters.

        Team + date/from/to requires a season, which can also be blocked on
        Free. The global date feed works without a season. Share each feed
        across followed teams for this discovery pass to limit API usage.
        """
        if count <= 0:
            return {"response": [], "results": 0}
        now = datetime.now(timezone.utc)
        fixtures = {}
        for offset in range(settings.fixture_discovery_days):
            day = (now.date() + timedelta(days=offset)).isoformat()
            if self._fixtures_date_limit is not None and day >= self._fixtures_date_limit:
                break
            if day in self._fixtures_date_errors:
                error = self._fixtures_date_errors[day]
                if fixtures:
                    raise PartialFixtureDiscovery(error, fixtures, count)
                raise error
            if day not in self._fixtures_by_date:
                try:
                    self._fixtures_by_date[day] = await self.get_date_fixtures(day)
                except SportsApiError as exc:
                    # Free permits a short rolling window. Keep valid dates and
                    # stop at its boundary; never hide quota/auth/network errors.
                    plan = str(exc.errors.get("plan", ""))
                    if self._fixtures_by_date and "Free plans do not have access to this date" in plan:
                        self._fixtures_date_limit = day
                        break
                    self._fixtures_date_errors[day] = exc
                    if fixtures:
                        raise PartialFixtureDiscovery(exc, fixtures, count) from exc
                    raise
            for fixture in self._fixtures_by_date[day].get("response", []):
                teams = fixture.get("teams", {})
                if not any(str(teams.get(side, {}).get("id")) == str(team_external_id)
                           for side in ("home", "away")):
                    continue
                info = fixture["fixture"]
                kickoff = datetime.fromisoformat(info["date"].replace("Z", "+00:00"))
                if kickoff.date() >= now.date():
                    fixtures[info["id"]] = fixture
        upcoming = sorted(fixtures.values(), key=lambda fx: fx["fixture"]["date"])[:count]
        return {"response": upcoming, "results": len(upcoming)}

    async def get_date_fixtures(self, day: str, *, priority: str = 'low') -> dict[str, Any]:
        freshness = (settings.live_poll_interval_seconds if priority != 'low'
                     else settings.fixture_discovery_refresh_seconds)
        return await self._request('/fixtures', {'date': day}, priority=priority,
                                   cache_seconds=freshness)

    def begin_fixture_discovery(self) -> None:
        """Refresh date feeds each pass, including in the persistent poller."""
        self._fixtures_by_date.clear()
        self._fixtures_date_errors.clear()
        self._fixtures_date_limit = None
