from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.routers.matches import _data_warning
from app.services import team_service
from app.sports_api.client import SportsApiClient, SportsApiError
from app.workers import discovery


async def test_missing_key_fails_before_making_network_request(monkeypatch):
    monkeypatch.setattr(settings, "sports_api_key", "")
    client = SportsApiClient()
    get = AsyncMock()
    monkeypatch.setattr(client._client, "get", get)
    try:
        with pytest.raises(SportsApiError, match="SPORTS_API_KEY is missing"):
            await client.get_live_fixtures()
        get.assert_not_awaited()
    finally:
        await client.aclose()


async def test_search_outage_preserves_cache_with_warning(monkeypatch):
    cached = [{"id": "saved", "name": "Arsenal"}]
    monkeypatch.setattr(team_service.teams_repo, "search_teams_cached", AsyncMock(return_value=cached))
    client = SimpleNamespace(search_teams=AsyncMock(side_effect=SportsApiError("quota")))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(sports_client=client)))
    result = await team_service.search_teams(request, "Arsenal")
    assert result["teams"] == cached
    assert "unavailable" in result["warning"]


def test_live_data_warning_distinguishes_fresh_stale_and_missing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "sports_api_key", "test")
    now = datetime.now(timezone.utc)
    assert _data_warning([{"status": "live", "last_polled_at": now}]) is None
    assert "out of date" in _data_warning([
        {"status": "live", "last_polled_at": now - timedelta(
            seconds=settings.live_poll_interval_seconds + 121)},
    ])
    monkeypatch.setattr(settings, "sports_api_key", "")
    assert "not configured" in _data_warning([])


async def test_name_repair_uses_matching_verified_profile(monkeypatch):
    monkeypatch.setattr(discovery.teams_repo, "find_teams_needing_name_repair", AsyncMock(
        return_value=[{"external_id": "100"}],
    ))
    upsert = AsyncMock()
    monkeypatch.setattr(discovery.teams_repo, "upsert_team", upsert)
    client = SimpleNamespace(get_team=AsyncMock(return_value={"response": [
        {"team": {"id": 999, "name": "Wrong club"}},
        {"team": {"id": 100, "name": "Home FC"}},
    ]}))
    assert await discovery.repair_team_names(client) == 1
    upsert.assert_awaited_once_with("100", "Home FC", None)


async def test_discovery_failure_does_not_fall_back_to_per_team_live_calls(monkeypatch):
    client = SimpleNamespace(
        get_team_live_fixtures=AsyncMock(),
        get_team_fixtures=AsyncMock(side_effect=SportsApiError("plan restriction")),
    )
    with pytest.raises(SportsApiError, match="plan restriction"):
        await discovery.discover_team(client, "100")
    client.get_team_live_fixtures.assert_not_awaited()


async def test_partial_date_discovery_persists_available_fixtures_and_reports_failure(monkeypatch):
    from app.sports_api.client import PartialFixtureDiscovery
    from app.sports_api.mock import build_timeline
    raw = build_timeline('live')[0][1]['response'][0]
    raw['fixture']['date'] = datetime.now(timezone.utc).isoformat()
    error = PartialFixtureDiscovery(SportsApiError('outage', 503), {'live': raw}, 10)
    client = SimpleNamespace(get_team_fixtures=AsyncMock(side_effect=error))
    monkeypatch.setattr(discovery.teams_repo, 'upsert_team', AsyncMock(return_value={'id': 'team'}))
    upsert = AsyncMock()
    refreshed = AsyncMock()
    monkeypatch.setattr(discovery.matches_repo, 'upsert_match', upsert)
    monkeypatch.setattr(discovery.teams_repo, 'mark_discovered', refreshed)
    with pytest.raises(PartialFixtureDiscovery):
        await discovery.discover_team(client, '100')
    assert upsert.await_args.args[3] == 'live'
    refreshed.assert_not_awaited()


async def test_budget_skip_keeps_partial_discovery_without_reporting_api_failure(monkeypatch):
    from app.sports_api.client import ApiRequestSkipped, PartialFixtureDiscovery
    from app.sports_api.mock import build_timeline
    raw = build_timeline('live')[0][1]['response'][0]
    raw['fixture']['date'] = datetime.now(timezone.utc).isoformat()
    error = PartialFixtureDiscovery(ApiRequestSkipped('priority_reserve'), {'live': raw}, 10)
    client = SimpleNamespace(get_team_fixtures=AsyncMock(side_effect=error))
    monkeypatch.setattr(discovery.teams_repo, 'upsert_team', AsyncMock(return_value={'id': 'team'}))
    upsert = AsyncMock()
    monkeypatch.setattr(discovery.matches_repo, 'upsert_match', upsert)
    with pytest.raises(ApiRequestSkipped):
        await discovery.discover_team(client, '100')
    upsert.assert_awaited_once()
