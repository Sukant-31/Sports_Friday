from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.sports_api.client import SportsApiClient
from app.config import settings


def fixture(fid, home, away, day):
    return {'fixture': {'id': fid, 'date': day.isoformat()},
            'teams': {'home': {'id': home}, 'away': {'id': away}}}


async def test_free_discovery_filters_sorts_limits_and_shares_date_feeds(monkeypatch):
    monkeypatch.setattr(settings, 'fixture_discovery_days', 7)
    client = SportsApiClient()
    now = datetime.now(timezone.utc)
    rows = [fixture(3, 7937, 2, now + timedelta(days=3)),
            fixture(1, 2, 7937, now + timedelta(days=1)),
            fixture(2, 7937, 2, now + timedelta(days=2)),
            fixture(4, 99, 98, now + timedelta(days=1)),
            fixture(5, 7937, 2, now - timedelta(days=1))]
    fetch = AsyncMock(return_value={'response': rows})
    monkeypatch.setattr(client, '_request', fetch)
    try:
        result = await client.get_team_fixtures('7937', 2)
        assert [fx['fixture']['id'] for fx in result['response']] == [1, 2]
        assert result['results'] == 2
        assert fetch.await_count == 7
        dates = [call.args[1]['date'] for call in fetch.await_args_list]
        assert dates == [(now.date() + timedelta(days=i)).isoformat() for i in range(7)]
        assert all(call.args[0] == '/fixtures' and set(call.args[1]) == {'date'}
                   for call in fetch.await_args_list)
        other = await client.get_team_fixtures('99', 10)
        assert [fx['fixture']['id'] for fx in other['response']] == [4]
        assert fetch.await_count == 7
        client.begin_fixture_discovery()
        await client.get_team_fixtures('7937', 10)
        assert fetch.await_count == 14
    finally:
        await client.aclose()


async def test_zero_fixture_limit_does_not_call_api(monkeypatch):
    client = SportsApiClient()
    fetch = AsyncMock()
    monkeypatch.setattr(client, '_request', fetch)
    try:
        assert await client.get_team_fixtures('7937', 0) == {'response': [], 'results': 0}
        fetch.assert_not_awaited()
    finally:
        await client.aclose()


async def test_free_date_boundary_preserves_fixtures_and_is_shared(monkeypatch):
    from app.sports_api.client import SportsApiError
    monkeypatch.setattr(settings, 'fixture_discovery_days', 7)
    client = SportsApiClient()
    now = datetime.now(timezone.utc)
    tomorrow = fixture(1, 7937, 529, now + timedelta(days=1))
    error = SportsApiError('date restricted', errors={
        'plan': 'Free plans do not have access to this date, try today and tomorrow.'})
    fetch = AsyncMock(side_effect=[{'response': []}, {'response': [tomorrow]}, error])
    monkeypatch.setattr(client, '_request', fetch)
    try:
        assert (await client.get_team_fixtures('7937', 10))['response'] == [tomorrow]
        assert (await client.get_team_fixtures('529', 10))['response'] == [tomorrow]
        assert fetch.await_count == 3
    finally:
        await client.aclose()


async def test_non_date_errors_are_not_hidden(monkeypatch):
    import pytest
    from app.sports_api.client import SportsApiError
    client = SportsApiClient()
    fetch = AsyncMock(side_effect=[{'response': []}, SportsApiError('quota exceeded')])
    monkeypatch.setattr(client, '_request', fetch)
    try:
        with pytest.raises(SportsApiError, match='quota exceeded'):
            await client.get_team_fixtures('7937', 10)
    finally:
        await client.aclose()


async def test_default_free_window_only_fetches_today_and_tomorrow(monkeypatch):
    monkeypatch.setattr(settings, 'fixture_discovery_days', 2)
    client = SportsApiClient()
    fetch = AsyncMock(return_value={'response': []})
    monkeypatch.setattr(client, '_request', fetch)
    try:
        for team in range(8):
            await client.get_team_fixtures(str(team), 10)
        assert fetch.await_count == 2
        assert all(set(call.args[1]) == {'date'} for call in fetch.await_args_list)
    finally:
        await client.aclose()


async def test_failed_discovery_feed_is_not_refetched_for_every_team(monkeypatch):
    import pytest
    from app.sports_api.client import SportsApiError
    client = SportsApiClient()
    fetch = AsyncMock(side_effect=SportsApiError('upstream unavailable', 503))
    monkeypatch.setattr(client, '_request', fetch)
    try:
        for team in range(8):
            with pytest.raises(SportsApiError):
                await client.get_team_fixtures(str(team), 10)
        assert fetch.await_count == 1
        client.begin_fixture_discovery()
        with pytest.raises(SportsApiError):
            await client.get_team_fixtures('0', 10)
        assert fetch.await_count == 2
    finally:
        await client.aclose()


async def test_later_feed_failure_keeps_matching_earlier_results(monkeypatch):
    import pytest
    from app.sports_api.client import PartialFixtureDiscovery, SportsApiError
    client = SportsApiClient()
    today = fixture(1, 7937, 529, datetime.now(timezone.utc))
    fetch = AsyncMock(side_effect=[{'response': [today]}, SportsApiError('outage', 503)])
    monkeypatch.setattr(client, '_request', fetch)
    try:
        for team in ('7937', '529'):
            with pytest.raises(PartialFixtureDiscovery) as caught:
                await client.get_team_fixtures(team, 10)
            assert caught.value.fixtures['response'] == [today]
        assert fetch.await_count == 2
    finally:
        await client.aclose()
