from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from app.config import settings
from app.deps import get_current_user_id
from app.main import create_app
from app.routers import push


@pytest_asyncio.fixture
async def setup(monkeypatch):
    user_id = uuid4()
    app = create_app()
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    monkeypatch.setattr(push.limiter, 'enabled', False)
    monkeypatch.setattr(settings, 'push_transport', 'webpush')
    monkeypatch.setattr(settings, 'vapid_private_key', 'test-key')
    target = {'push_id': uuid4(), 'endpoint': 'https://push.example/current',
              'p256dh': 'key', 'auth': 'auth'}
    lookup = AsyncMock(return_value=target)
    send = AsyncMock()
    monkeypatch.setattr(push.push_repo, 'find_subscription_for_user', lookup)
    monkeypatch.setattr(push, 'send_push', send)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://test') as client:
        yield client, app, user_id, target, lookup, send


async def test_sends_only_saved_current_user_subscription(setup):
    client, app, user, target, lookup, send = setup
    result = await client.post('/api/push/test', json={'endpoint': target['endpoint']})
    assert result.status_code == 200
    assert result.json()['status'] == 'accepted'
    assert all(call.args == (user, target['endpoint']) for call in lookup.await_args_list)
    send.assert_awaited_once_with(target, {
        'title': 'Sports Friday test', 'body': 'Your browser push notification is working.',
        'tag': 'sports-friday-push-test'})
    assert 'auth' not in result.json()


async def test_requires_authentication(setup):
    client, app, user, target, lookup, send = setup
    app.dependency_overrides.clear()
    result = await client.post('/api/push/test', json={'endpoint': target['endpoint']})
    assert result.status_code == 401
    lookup.assert_not_awaited()
    send.assert_not_awaited()


async def test_cannot_send_to_other_or_unregistered_subscription(setup):
    client, app, user, target, lookup, send = setup
    lookup.return_value = None
    result = await client.post('/api/push/test', json={'endpoint': 'https://push.example/other'})
    assert result.status_code == 404
    lookup.assert_awaited_once_with(user, 'https://push.example/other')
    send.assert_not_awaited()


@pytest.mark.parametrize('transport,key', [('console', 'key'), ('webpush', '')])
async def test_configuration_failure_does_not_claim_delivery(setup, monkeypatch, transport, key):
    client, app, user, target, lookup, send = setup
    monkeypatch.setattr(settings, 'push_transport', transport)
    monkeypatch.setattr(settings, 'vapid_private_key', key)
    result = await client.post('/api/push/test', json={'endpoint': target['endpoint']})
    assert result.status_code == 503
    send.assert_not_awaited()


async def test_push_failure_returns_safe_error(setup):
    client, app, user, target, lookup, send = setup
    send.side_effect = RuntimeError('private key material')
    result = await client.post('/api/push/test', json={'endpoint': target['endpoint']})
    assert result.status_code == 502
    assert 'private key material' not in result.text


async def test_expired_subscription_is_not_reported_as_accepted(setup):
    client, app, user, target, lookup, send = setup
    lookup.side_effect = [target, None]
    result = await client.post('/api/push/test', json={'endpoint': target['endpoint']})
    assert result.status_code == 410
    send.assert_awaited_once()


async def test_repository_lookup_is_scoped_to_user(monkeypatch):
    import asyncpg
    from app.repositories import push_subscriptions as repo
    conn = await asyncpg.connect(settings.database_url, timeout=3)
    owner, other = uuid4(), uuid4()
    try:
        await conn.execute('CREATE TEMP TABLE push_subscriptions '
                           '(id uuid, user_id uuid, endpoint text, p256dh text, auth text)')
        await conn.execute('INSERT INTO push_subscriptions VALUES ($1,$2,$3,$4,$5)',
                           uuid4(), owner, 'https://push.example/owned', 'key', 'auth')
        monkeypatch.setattr(repo.db, 'fetchrow', conn.fetchrow)
        assert await repo.find_subscription_for_user(other, 'https://push.example/owned') is None
        found = await repo.find_subscription_for_user(owner, 'https://push.example/owned')
        assert found['endpoint'] == 'https://push.example/owned'
    finally:
        await conn.close()
