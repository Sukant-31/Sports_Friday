from unittest.mock import AsyncMock

import asyncpg
import httpx
import pytest

from app.config import settings
from app.main import create_app
from app.notification_schema import _ensure_schema
from app.repositories import match_events
from app.workers import cron_poll, poller


async def legacy_connection():
    conn = await asyncpg.connect(settings.database_url, timeout=3)
    await conn.execute('SET search_path TO pg_temp')
    await conn.execute('''
        CREATE TEMP TABLE match_events (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(), match_id uuid,
          team_id uuid, type text, detail jsonb DEFAULT '{}',
          created_at timestamptz DEFAULT now());
        CREATE TEMP TABLE push_subscriptions (id uuid PRIMARY KEY);
        CREATE TEMP TABLE subscriptions (notify_cards boolean DEFAULT false);
        INSERT INTO match_events (type) VALUES ('goal');
        INSERT INTO subscriptions (notify_cards) VALUES (false);
    ''')
    return conn


async def test_schema_upgrade_backfills_history_and_preserves_new_pending_events():
    conn = await legacy_connection()
    try:
        with pytest.raises(asyncpg.UndefinedColumnError, match='delivered_at'):
            await conn.fetch('SELECT id FROM match_events WHERE delivered_at IS NULL')
        await _ensure_schema(conn)
        assert await conn.fetchval('SELECT delivered_at IS NOT NULL FROM match_events')
        assert await conn.fetchval('SELECT notify_cards FROM subscriptions') is False
        new_id = await conn.fetchval("INSERT INTO match_events (type) VALUES ('goal') RETURNING id")
        await _ensure_schema(conn)
        assert await conn.fetchval('SELECT delivered_at FROM match_events WHERE id=$1', new_id) is None
        assert await conn.fetchval("SELECT count(*) FROM schema_migrations WHERE name='006_notification_delivery.sql'") == 1
        assert await conn.fetchval("SELECT to_regclass('notification_deliveries') IS NOT NULL")
        assert await conn.fetchval("SELECT to_regclass('match_events_pending_idx') IS NOT NULL")
    finally:
        await conn.close()


async def test_cron_route_recovers_missing_column_before_polling(monkeypatch, cron_runtime):
    conn = await legacy_connection()
    client = cron_runtime()
    async def ensure():
        await _ensure_schema(conn)
    monkeypatch.setattr(settings, 'cron_secret', 'test-secret')
    monkeypatch.setattr(cron_poll, 'ensure_notification_delivery_schema', ensure)
    monkeypatch.setattr(cron_poll, 'SportsApiClient', lambda: client)
    monkeypatch.setattr(cron_poll, 'discover', AsyncMock(return_value=1))
    monkeypatch.setattr(poller.matches_repo, 'find_pollable_matches', AsyncMock(return_value=[]))
    monkeypatch.setattr(match_events.db, 'fetch', conn.fetch)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                    base_url='http://test') as http:
            result = await http.get('/api/cron/poll', headers={'Authorization': 'Bearer test-secret'})
            assert result.status_code == 200
            assert result.json()['discovered'] == 1
            assert result.json()['ok'] is True
        client.aclose.assert_awaited_once()
    finally:
        await conn.close()
