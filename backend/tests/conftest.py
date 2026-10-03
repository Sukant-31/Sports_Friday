from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.workers import cron_poll


@pytest.fixture(autouse=True)
def isolated_http_rate_limit_namespace(monkeypatch):
    """Retain real Redis enforcement without counters leaking between tests/runs."""
    from app.rate_limit import limiter
    monkeypatch.setattr(limiter, '_key_prefix', f'test:http-limit:{uuid4()}')


@pytest.fixture
def cron_runtime(monkeypatch):
    """Stub DB coordination for focused cron unit/legacy-schema tests."""
    @asynccontextmanager
    async def lock(*args):
        yield True
    monkeypatch.setattr(cron_poll, 'ensure_polling_schema', AsyncMock())
    monkeypatch.setattr(cron_poll.sports_requests, 'lock', lock)
    monkeypatch.setattr(cron_poll.sports_requests, 'budget', AsyncMock(return_value={
        'limit': 90, 'used_estimate': 0, 'remaining': 90, 'application_requests': 0,
        'window': 'rolling_24_hours'}))
    monkeypatch.setattr(cron_poll.matches_repo, 'polling_counts', AsyncMock(return_value={
        'stored_fixtures': 0, 'live_fixtures': 0}))
    monkeypatch.setattr(cron_poll, '_receipt_count', AsyncMock(return_value=0))
    def client():
        stub = AsyncMock()
        stub.scope = 'unit-test'
        stub.stats = {'attempted': 0, 'made': 0, 'skipped': 0, 'cache_hits': 0, 'skip_reasons': {}}
        return stub
    return client
