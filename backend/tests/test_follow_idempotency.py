"""Follow isolation and concurrent preference preservation on local PostgreSQL."""
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from app import db
from app.deps import get_current_user_id
from app.main import create_app
from app.repositories import users
from app.services import subscription_service

PREFS = ('notify_goals', 'notify_cards', 'notify_match_status')


def prefs(row):
    return tuple(row[key] for key in PREFS)


@pytest_asyncio.fixture
async def accounts(monkeypatch):
    await db.connect()
    run = uuid4().hex
    user_ids, team_ids = [], []
    monkeypatch.setattr(subscription_service, 'discover_team', AsyncMock())
    try:
        for label in ('a', 'b'):
            user = await users.create_user(f'follow-{label}-{run}@example.com', 'test-hash')
            user_ids.append(user['id'])
            team = await db.fetchrow('INSERT INTO teams (external_id,name) VALUES ($1,$2) RETURNING id',
                                    f'follow-{label}-{run}', f'Follow {label}')
            team_ids.append(team['id'])
        yield user_ids, team_ids
    finally:
        for uid in user_ids:
            await db.execute('DELETE FROM users WHERE id=$1', uid)
        for tid in team_ids:
            await db.execute('DELETE FROM teams WHERE id=$1', tid)
        await db.disconnect()


@asynccontextmanager
async def client_for(uid):
    app = create_app()
    app.state.sports_client = object()
    app.dependency_overrides[get_current_user_id] = lambda: uid
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield client


async def follow(client, tid, values=None):
    body = {'teamId': str(tid)}
    if values is not None:
        body.update(dict(zip(PREFS, values, strict=True)))
    response = await client.post('/api/subscriptions', json=body)
    assert response.status_code == 201, response.text
    return response.json()['subscription']


async def saved(uid, tid):
    return await db.fetchrow('SELECT * FROM subscriptions WHERE user_id=$1 AND team_id=$2', uid, tid)


def synchronize(monkeypatch):
    barrier, original = asyncio.Barrier(2), db.fetchrow
    async def query(sql, *args):
        if sql.lstrip().startswith(('INSERT INTO subscriptions', 'UPDATE subscriptions')):
            await barrier.wait()
        return await original(sql, *args)
    monkeypatch.setattr(db, 'fetchrow', query)


async def test_follow_a_and_b_independently(accounts):
    (uid, _), (a, b) = accounts
    async with client_for(uid) as client:
        first = await follow(client, a, (False, True, False))
        second = await follow(client, b)
    assert first['id'] != second['id']
    assert prefs(await saved(uid, a)) == (False, True, False)
    assert prefs(await saved(uid, b)) == (True, True, True)


async def test_patch_a_does_not_affect_b(accounts):
    (uid, _), (a, b) = accounts
    async with client_for(uid) as client:
        first = await follow(client, a)
        await follow(client, b, (False, True, False))
        before = dict(await saved(uid, b))
        response = await client.patch('/api/subscriptions/' + first['id'], json={'notifyGoals': False})
    assert response.status_code == 200
    assert prefs(await saved(uid, a)) == (False, True, True)
    assert dict(await saved(uid, b)) == before


async def test_unfollow_preserves_b_and_refollow_creates_fresh_subscription(accounts):
    (uid, _), (a, b) = accounts
    async with client_for(uid) as client:
        first = await follow(client, a, (False, False, False))
        await follow(client, b)
        before = dict(await saved(uid, b))
        assert (await client.delete('/api/subscriptions/' + first['id'])).status_code == 204
        assert await saved(uid, a) is None
        assert dict(await saved(uid, b)) == before
        fresh = await follow(client, a)
    assert fresh['id'] != first['id']
    assert prefs(await saved(uid, a)) == (True, True, True)
    assert dict(await saved(uid, b)) == before


async def test_repeat_follow_preserves_patched_false_values(accounts):
    (uid, _), (a, _) = accounts
    async with client_for(uid) as client:
        first = await follow(client, a)
        assert (await client.patch('/api/subscriptions/' + first['id'], json={
            'notifyGoals': False, 'notifyCards': False, 'notifyMatchStatus': False})).status_code == 200
        before = dict(await saved(uid, a))
        again = await follow(client, a)
    assert again['id'] == first['id']
    assert prefs(again) == (False, False, False)
    assert dict(await saved(uid, a)) == before


@pytest.mark.parametrize('existing', [False, True])
async def test_concurrent_duplicate_follows_preserve_one_row(accounts, monkeypatch, existing):
    (uid, _), (a, _) = accounts
    async with client_for(uid) as client:
        before = None
        if existing:
            await follow(client, a, (False, False, False))
            before = dict(await saved(uid, a))
        with monkeypatch.context() as race:
            synchronize(race)
            result = await asyncio.wait_for(asyncio.gather(
                follow(client, a, (False, True, False)),
                follow(client, a, (True, False, True))), timeout=10)
    rows = await db.fetch('SELECT * FROM subscriptions WHERE user_id=$1 AND team_id=$2', uid, a)
    assert len(rows) == 1
    assert result[0]['id'] == result[1]['id'] == str(rows[0]['id'])
    assert prefs(result[0]) == prefs(result[1]) == prefs(rows[0])
    if before is not None:
        assert dict(rows[0]) == before
    else:
        assert prefs(rows[0]) in {(False, True, False), (True, False, True)}


async def test_concurrent_follow_and_patch_does_not_reset_preferences(accounts, monkeypatch):
    (uid, _), (a, _) = accounts
    async with client_for(uid) as client:
        first = await follow(client, a)
        with monkeypatch.context() as race:
            synchronize(race)
            repeated, patched = await asyncio.wait_for(asyncio.gather(
                follow(client, a), client.patch('/api/subscriptions/' + first['id'], json={
                    'notifyGoals': False, 'notifyCards': False, 'notifyMatchStatus': False})), timeout=10)
    assert patched.status_code == 200
    assert repeated['id'] == first['id']
    assert prefs(await saved(uid, a)) == (False, False, False)


async def test_other_user_cannot_patch_or_delete(accounts):
    (owner, other), (a, _) = accounts
    async with client_for(owner) as client:
        original = await follow(client, a, (False, True, False))
    before = dict(await saved(owner, a))
    async with client_for(other) as client:
        assert (await client.patch('/api/subscriptions/' + original['id'],
                                  json={'notifyGoals': True})).status_code == 404
        assert (await client.delete('/api/subscriptions/' + original['id'])).status_code == 404
        own = await follow(client, a)
    assert own['id'] != original['id']
    assert prefs(await saved(other, a)) == (True, True, True)
    assert dict(await saved(owner, a)) == before


async def test_preexisting_stored_values_are_returned_unchanged(accounts):
    (uid, _), (a, _) = accounts
    row = await db.fetchrow('''INSERT INTO subscriptions
        (user_id,team_id,notify_goals,notify_cards,notify_match_status)
        VALUES ($1,$2,false,true,false) RETURNING *''', uid, a)
    before = dict(row)
    async with client_for(uid) as client:
        result = await follow(client, a, (True, False, True))
    assert result['id'] == str(before['id'])
    assert prefs(result) == (False, True, False)
    assert dict(await saved(uid, a)) == before
