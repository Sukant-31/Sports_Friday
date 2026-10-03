"""Shared-browser ownership regression tests; delivery is always mocked."""

import asyncio
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from app import db
from app.config import settings
from app.deps import get_current_user_id
from app.main import create_app
from app.repositories import push_subscriptions as repo
from app.repositories import subscriptions, users
from app.routers import push


@pytest_asyncio.fixture
async def accounts():
    await db.connect()
    run = uuid4().hex
    a = await users.create_user(f"push-a-{run}@example.com", "hash")
    b = await users.create_user(f"push-b-{run}@example.com", "hash")
    endpoint = f"https://updates.push.services.mozilla.com/wpush/v2/{run}"
    try:
        yield a['id'], b['id'], endpoint
    finally:
        await db.execute('DELETE FROM users WHERE id=ANY($1::uuid[])', [a['id'], b['id']])
        await db.disconnect()


async def test_logout_detach_preserves_other_devices_and_removes_old_recipient(accounts):
    a, b, endpoint = accounts
    other_device = endpoint + '-other'
    team = await db.fetchrow('INSERT INTO teams (external_id,name) VALUES ($1,$2) RETURNING id',
                            uuid4().hex, 'Isolation test team')
    match_id = uuid4()
    try:
        await subscriptions.create_subscription(a, team['id'], True, False, True)
        await repo.upsert_push_subscription(a, endpoint, 'key', 'auth')
        await repo.upsert_push_subscription(a, other_device, 'key', 'auth')
        assert len(await subscriptions.find_push_targets_for_event(team['id'], 'goal', match_id)) == 2
        assert await repo.delete_push_subscription_by_endpoint(a, endpoint)
        assert await repo.find_subscription_for_user(a, endpoint) is None
        assert await repo.find_subscription_for_user(a, other_device) is not None
        targets = await subscriptions.find_push_targets_for_event(team['id'], 'goal', match_id)
        assert [t['endpoint'] for t in targets] == [other_device]
        assert await subscriptions.find_push_targets_for_event(team['id'], 'card', match_id) == []
        # A detached endpoint can be registered by B without reassigning A's rows.
        await repo.upsert_push_subscription(b, endpoint, 'b-key', 'b-auth')
        assert await repo.find_subscription_for_user(a, endpoint) is None
        assert (await repo.find_subscription_for_user(b, endpoint))['p256dh'] == 'b-key'
        assert [t['endpoint'] for t in await subscriptions.find_push_targets_for_event(
            team['id'], 'goal', match_id)] == [other_device]
    finally:
        await db.execute('DELETE FROM teams WHERE id=$1', team['id'])


async def test_cross_account_registration_is_rejected_without_reassignment(accounts):
    a, b, endpoint = accounts
    first = await repo.upsert_push_subscription(a, endpoint, 'a-key', 'a-auth')
    with pytest.raises(repo.PushOwnershipConflict):
        await repo.upsert_push_subscription(b, endpoint, 'b-key', 'b-auth')
    assert await repo.find_subscription_for_user(b, endpoint) is None
    assert (await repo.find_subscription_for_user(a, endpoint))['p256dh'] == 'a-key'
    updated = await repo.upsert_push_subscription(a, endpoint, 'updated', 'auth')
    assert updated['id'] == first['id']


async def test_concurrent_registration_cannot_create_two_owners(accounts):
    a, b, endpoint = accounts
    results = await asyncio.gather(
        repo.upsert_push_subscription(a, endpoint, 'a-key', 'auth'),
        repo.upsert_push_subscription(b, endpoint, 'b-key', 'auth'),
        return_exceptions=True,
    )
    assert sum(isinstance(result, repo.PushOwnershipConflict) for result in results) == 1
    assert await db.pool().fetchval(
        'SELECT count(*) FROM push_subscriptions WHERE endpoint=$1', endpoint) == 1


async def test_test_endpoint_rejects_other_owner_and_registration_returns_conflict(accounts, monkeypatch):
    from unittest.mock import AsyncMock

    a, b, endpoint = accounts
    await repo.upsert_push_subscription(a, endpoint, 'key', 'auth')
    app = create_app()
    app.dependency_overrides[get_current_user_id] = lambda: b
    monkeypatch.setattr(push.limiter, 'enabled', False)
    monkeypatch.setattr(settings, 'push_transport', 'webpush')
    monkeypatch.setattr(settings, 'vapid_private_key', 'mock-only')
    send = AsyncMock()
    monkeypatch.setattr(push, 'send_push', send)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://test') as client:
        assert (await client.post('/api/push/test', json={'endpoint': endpoint})).status_code == 404
        registration = await client.post('/api/push/subscribe', json={
            'endpoint': endpoint, 'keys': {'p256dh': 'key', 'auth': 'auth'}})
        assert registration.status_code == 409
        # B's scoped deletion cannot remove A's association.
        assert (await client.request('DELETE', '/api/push/subscribe',
                                    json={'endpoint': endpoint})).status_code == 204
        assert await repo.find_subscription_for_user(a, endpoint) is not None
        # Following A's cleanup, B's own registration/test works without bypassing ownership.
        await repo.delete_push_subscription_by_endpoint(a, endpoint)
        assert (await client.post('/api/push/subscribe', json={
            'endpoint': endpoint, 'keys': {'p256dh': 'key', 'auth': 'auth'}})).status_code == 201
        assert (await client.post('/api/push/test', json={'endpoint': endpoint})).status_code == 200
        app.dependency_overrides[get_current_user_id] = lambda: a
        assert (await client.post('/api/push/test', json={'endpoint': endpoint})).status_code == 404
    send.assert_awaited_once()
