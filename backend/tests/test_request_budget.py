import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from app import db
from app.config import settings
from app.polling_schema import ensure_polling_schema
from app.repositories import sports_requests as repo
from app.sports_api.client import ApiRequestSkipped, SportsApiClient, SportsApiError


@pytest_asyncio.fixture
async def budget_scope(monkeypatch):
    await db.connect()
    await ensure_polling_schema()
    monkeypatch.setattr(settings, 'sports_api_key', 'test-key-not-a-secret')
    monkeypatch.setattr(settings, 'max_daily_api_requests', 90)
    monkeypatch.setattr(settings, 'api_priority_reserve', 15)
    scope = 'test:' + uuid4().hex
    yield scope
    await db.execute('DELETE FROM sports_api_requests WHERE scope=$1', scope)
    await db.execute('DELETE FROM sports_api_cache WHERE scope=$1', scope)
    await db.disconnect()


async def fake_client(scope, handler):
    client = SportsApiClient()
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url='https://provider.example',
                                      transport=httpx.MockTransport(handler))
    client.scope = scope
    return client


async def test_atomic_budget_reservations_across_concurrent_instances(budget_scope):
    accepted = await asyncio.gather(*(repo.reserve(budget_scope, 7) for _ in range(20)))
    assert sum(accepted) == 7
    result = await repo.budget(budget_scope, 90)
    assert result['application_requests'] == 7 and result['remaining'] == 83


async def test_rolling_window_expires_by_age_not_midnight(budget_scope):
    await db.execute("INSERT INTO sports_api_requests(scope,requested_at,units) VALUES "
                     "($1,clock_timestamp()-INTERVAL '25 hours',40), "
                     "($1,clock_timestamp()-INTERVAL '23 hours',10)", budget_scope)
    result = await repo.budget(budget_scope, 90)
    assert result['used_estimate'] == 10
    assert result['window'] == 'rolling_24_hours'
    assert await repo.reserve(budget_scope, 11)
    assert not await repo.reserve(budget_scope, 11)


async def test_nearly_exhausted_budget_reserves_capacity_for_live_matches(budget_scope):
    await repo.reconcile(budget_scope, 75)
    client = await fake_client(budget_scope, lambda request: httpx.Response(200, json={'response': []}))
    try:
        with pytest.raises(ApiRequestSkipped, match='priority_reserve'):
            await client.get_date_fixtures('2026-10-02')
        assert client.stats['made'] == 0
        await client.get_live_fixtures()
        assert client.stats['made'] == 1
        assert (await repo.budget(budget_scope, 90))['remaining'] == 14
    finally:
        await client.aclose()


async def test_exhausted_budget_skips_all_http_calls(budget_scope):
    await repo.reconcile(budget_scope, 90)
    reached = []
    def handler(request):
        reached.append(request)
        return httpx.Response(200, json={'response': []})
    client = await fake_client(budget_scope, handler)
    try:
        for priority in ('high', 'low'):
            with pytest.raises(ApiRequestSkipped, match='budget_exhausted'):
                await client._request('/fixtures', {'date': '2026-10-02'}, priority=priority)
        assert reached == []
        assert client.stats['made'] == 0 and client.stats['skipped'] == 2
    finally:
        await client.aclose()


async def test_provider_headers_include_other_consumers_without_resetting_local_usage(budget_scope):
    def handler(request):
        return httpx.Response(200, json={'response': []}, headers={
            'x-ratelimit-requests-limit': '100', 'x-ratelimit-requests-remaining': '20'})
    client = await fake_client(budget_scope, handler)
    try:
        await client._request('/fixtures', {'live': 'all'}, priority='high')
        result = await repo.budget(budget_scope, 90)
        assert result['used_estimate'] == 80 and result['application_requests'] == 1
        await repo.reconcile(budget_scope, 0)
        assert (await repo.budget(budget_scope, 90))['used_estimate'] == 80
    finally:
        await client.aclose()


async def test_each_http_retry_counts_as_one_request(budget_scope, monkeypatch):
    from unittest.mock import AsyncMock
    from app.sports_api import client as module
    monkeypatch.setattr(module.asyncio, 'sleep', AsyncMock())
    client = await fake_client(budget_scope, lambda request: httpx.Response(503, json={}))
    try:
        with pytest.raises(SportsApiError):
            await client._request('/fixtures', {'live': 'all'}, priority='high')
        assert client.stats['made'] == 4
        assert (await repo.budget(budget_scope, 90))['application_requests'] == 4
    finally:
        await client.aclose()


async def test_new_client_reuses_postgres_feed_cache(budget_scope):
    calls = []
    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, json={'response': []})
    first = await fake_client(budget_scope, handler)
    second = await fake_client(budget_scope, handler)
    try:
        await first.get_date_fixtures('2026-10-02')
        await second.get_date_fixtures('2026-10-02')
        assert len(calls) == 1 and second.stats['cache_hits'] == 1
        assert (await repo.budget(budget_scope, 90))['application_requests'] == 1
    finally:
        await first.aclose()
        await second.aclose()


async def test_high_priority_does_not_reuse_stale_discovery_cache(budget_scope):
    calls = []
    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, json={'response': []})
    client = await fake_client(budget_scope, handler)
    try:
        await client.get_date_fixtures('2026-10-02')
        await db.execute("UPDATE sports_api_cache SET fetched_at=clock_timestamp()-INTERVAL '10 minutes' "
                         "WHERE scope=$1", budget_scope)
        await client.get_date_fixtures('2026-10-02', priority='high')
        assert len(calls) == 2
    finally:
        await client.aclose()


async def test_overlapping_feed_fetch_is_skipped_instead_of_duplicated(budget_scope):
    key = '/fixtures:{"live": "all"}'
    client = await fake_client(budget_scope, lambda request: httpx.Response(200, json={'response': []}))
    try:
        async with repo.lock(budget_scope, 'cache:' + key) as held:
            assert held
            with pytest.raises(ApiRequestSkipped, match='concurrent_request'):
                await client.get_live_fixtures()
        assert client.stats['made'] == 0
    finally:
        await client.aclose()


async def test_polling_migration_is_packaged_and_idempotent(budget_scope):
    packaged = Path(__file__).parents[1] / 'app/sql/008_intelligent_polling.sql'
    canonical = Path(__file__).parents[2] / 'migrations/008_intelligent_polling.sql'
    assert packaged.read_bytes() == canonical.read_bytes()
    await db.execute(packaged.read_text())
    await db.execute(packaged.read_text())
    assert (await repo.budget(budget_scope, 90))['remaining'] == 90


async def test_budget_stopped_retry_does_not_hide_upstream_failure(budget_scope, monkeypatch):
    from unittest.mock import AsyncMock
    from app.sports_api import client as module
    monkeypatch.setattr(module.asyncio, 'sleep', AsyncMock())
    monkeypatch.setattr(settings, 'max_daily_api_requests', 1)
    client = await fake_client(budget_scope, lambda request: httpx.Response(503, json={}))
    try:
        with pytest.raises(SportsApiError, match='Upstream failure') as caught:
            await client.get_live_fixtures()
        assert not isinstance(caught.value, ApiRequestSkipped)
        assert caught.value.status_code == 503
        assert client.stats['made'] == 1 and client.stats['skipped'] == 1
    finally:
        await client.aclose()
