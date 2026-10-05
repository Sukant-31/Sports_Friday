"""Device admission uses real PostgreSQL transactions; push network I/O is mocked."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from redis.exceptions import ConnectionError
from pywebpush import WebPushException
from requests import Response

from app import db
from app.config import settings
from app.deps import get_current_user_id
from app.main import create_app
from app.rate_limit import limiter
from app.security import create_token
from app.repositories import push_subscriptions as repo
from app.repositories import users
from app.workers import web_push


def endpoint():
    return f"https://updates.push.services.mozilla.com/wpush/v2/{uuid4().hex}"


async def register(client, url, key="key"):
    return await client.post("/api/push/subscribe", json={
        "endpoint": url, "keys": {"p256dh": key, "auth": "auth"}})


async def count(uid):
    return await db.pool().fetchval("SELECT count(*) FROM push_subscriptions WHERE user_id=$1", uid)


async def seed(uid, number):
    urls = [endpoint() for _ in range(number)]
    for url in urls:
        await repo.upsert_push_subscription(uid, url, "key", "auth")
    return urls


@pytest_asyncio.fixture
async def accounts():
    await db.connect()
    ids, clients = [], []
    try:
        for _ in range(2):
            user = await users.create_user(f"device-limit-{uuid4().hex}@example.com", "test-hash")
            uid = user["id"]
            ids.append(uid)
            app = create_app()
            app.dependency_overrides[get_current_user_id] = lambda uid=uid: uid
            clients.append(httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                              base_url="https://test",
                                              cookies={settings.auth_cookie_name: create_token(str(uid))}))
        yield ids, clients
    finally:
        for client in clients:
            await client.aclose()
        await db.execute("DELETE FROM users WHERE id=ANY($1::uuid[])", ids)
        await db.disconnect()


async def test_ten_devices_allowed_eleventh_rejected_without_insert(accounts):
    ids, clients = accounts
    for _ in range(10):
        assert (await register(clients[0], endpoint())).status_code == 201
    rejected = endpoint()
    response = await register(clients[0], rejected)
    assert response.status_code == 409
    assert response.json() == {"detail": "Push subscription device limit reached"}
    assert await count(ids[0]) == 10
    assert await repo.find_subscription_for_user(ids[0], rejected) is None


@pytest.mark.parametrize("size", [10, 12])
async def test_refresh_at_or_above_cap_preserves_id_and_all_legacy_rows(accounts, size):
    ids, clients = accounts
    urls = await seed(ids[0], 10)
    # Simulate rows predating the cap, without routing them through admission.
    for _ in range(size - 10):
        await db.execute("INSERT INTO push_subscriptions(user_id,endpoint,p256dh,auth) "
                         "VALUES ($1,$2,'legacy','auth')", ids[0], endpoint())
    before = await db.pool().fetch("SELECT id FROM push_subscriptions WHERE user_id=$1", ids[0])
    target = await repo.find_subscription_for_user(ids[0], urls[0])
    assert (await register(clients[0], urls[0], "refreshed")).status_code == 201
    updated = await repo.find_subscription_for_user(ids[0], urls[0])
    assert updated["push_id"] == target["push_id"]
    assert updated["p256dh"] == "refreshed"
    assert (await register(clients[0], endpoint())).status_code == 409
    after = await db.pool().fetch("SELECT id FROM push_subscriptions WHERE user_id=$1", ids[0])
    assert {r["id"] for r in before} == {r["id"] for r in after}
    assert await count(ids[0]) == size


@pytest.mark.parametrize("contenders", [2, 8])
async def test_concurrent_distinct_endpoints_cannot_exceed_last_slot(accounts, contenders):
    ids, clients = accounts
    await seed(ids[0], 9)
    urls = [endpoint() for _ in range(contenders)]
    responses = await asyncio.gather(*(register(clients[0], url) for url in urls))
    assert sum(r.status_code == 201 for r in responses) == 1
    assert sum(r.status_code == 409 for r in responses) == contenders - 1
    assert await count(ids[0]) == 10
    for url, response in zip(urls, responses, strict=True):
        stored = await repo.find_subscription_for_user(ids[0], url)
        assert (stored is not None) == (response.status_code == 201)
        if response.status_code == 409:
            assert response.json()["detail"] == "Push subscription device limit reached"


async def test_concurrent_identical_endpoints_consume_one_slot(accounts):
    ids, clients = accounts
    await seed(ids[0], 9)
    url = endpoint()
    responses = await asyncio.gather(*(register(clients[0], url) for _ in range(6)))
    assert all(r.status_code == 201 for r in responses)
    assert await count(ids[0]) == 10


async def test_refresh_and_new_endpoints_compete_safely(accounts):
    ids, clients = accounts
    urls = await seed(ids[0], 9)
    before = await repo.find_subscription_for_user(ids[0], urls[0])
    refreshed, *added = await asyncio.gather(
        register(clients[0], urls[0], "refreshed"),
        register(clients[0], endpoint()), register(clients[0], endpoint()))
    assert refreshed.status_code == 201
    assert sorted(r.status_code for r in added) == [201, 409]
    assert await count(ids[0]) == 10
    assert (await repo.find_subscription_for_user(ids[0], urls[0]))["push_id"] == before["push_id"]


async def test_users_have_independent_limits_and_cannot_unsubscribe_each_other(accounts):
    ids, clients = accounts
    urls = await asyncio.gather(seed(ids[0], 10), seed(ids[1], 10))
    for client in clients:
        assert (await register(client, endpoint())).status_code == 409
    assert (await clients[1].request("DELETE", "/api/push/subscribe",
                                    json={"endpoint": urls[0][0]})).status_code == 204
    assert await count(ids[0]) == await count(ids[1]) == 10
    assert await repo.find_subscription_for_user(ids[0], urls[0][0]) is not None


async def test_cross_account_ownership_conflict_preserved_at_cap(accounts):
    ids, clients = accounts
    urls = await seed(ids[0], 10)
    await seed(ids[1], 10)
    response = await register(clients[1], urls[0])
    assert response.status_code == 409
    assert "still linked to another account" in response.json()["detail"]
    assert await repo.find_subscription_for_user(ids[1], urls[0]) is None
    assert await count(ids[0]) == await count(ids[1]) == 10


async def test_cross_account_concurrent_endpoint_keeps_one_owner(accounts):
    ids, clients = accounts
    await asyncio.gather(seed(ids[0], 9), seed(ids[1], 9))
    url = endpoint()
    responses = await asyncio.gather(*(register(client, url) for client in clients))
    assert sorted(r.status_code for r in responses) == [201, 409]
    assert sorted([await count(uid) for uid in ids]) == [9, 10]
    assert await db.pool().fetchval(
        "SELECT count(*) FROM push_subscriptions WHERE endpoint=$1", url) == 1


async def test_unsubscribe_frees_capacity(accounts):
    ids, clients = accounts
    urls = await seed(ids[0], 10)
    assert (await clients[0].request("DELETE", "/api/push/subscribe",
                                    json={"endpoint": urls[0]})).status_code == 204
    assert await count(ids[0]) == 9
    assert (await register(clients[0], endpoint())).status_code == 201
    assert await count(ids[0]) == 10


@pytest.mark.parametrize("status", [404, 410])
async def test_expired_provider_cleanup_frees_capacity(accounts, monkeypatch, status):
    ids, clients = accounts
    urls = await seed(ids[0], 10)
    target = dict(await repo.find_subscription_for_user(ids[0], urls[0]))
    response = Response()
    response.status_code = status

    def expired(*args):
        raise WebPushException("expired", response=response)

    monkeypatch.setattr(settings, "push_transport", "webpush")
    monkeypatch.setattr(settings, "vapid_private_key", "mock-only")
    monkeypatch.setattr(web_push, "_send", expired)
    assert await web_push.send_push(target, {"title": "Mock"}) == "expired"
    assert await count(ids[0]) == 9
    assert (await register(clients[0], endpoint())).status_code == 201


@pytest.mark.parametrize("phase", ["lock", "after_insert"])
async def test_transaction_failure_rolls_back_and_releases_locks(accounts, monkeypatch, phase):
    ids, _clients = accounts
    await seed(ids[0], 9)
    url = endpoint()
    pool = db.pool()
    async with pool.acquire() as conn:
        async def execute(query, *args):
            if phase == "lock" and "pg_advisory_xact_lock" in query:
                raise RuntimeError("mock lock failure")
            return await conn.execute(query, *args)

        async def fetchrow(query, *args):
            result = await conn.fetchrow(query, *args)
            if phase == "after_insert":
                raise RuntimeError("mock transaction failure after insert")
            return result

        proxy = SimpleNamespace(execute=execute, fetchrow=fetchrow,
                                fetchval=conn.fetchval, transaction=conn.transaction)

        @asynccontextmanager
        async def acquire():
            yield proxy

        with monkeypatch.context() as scoped:
            scoped.setattr(db, "pool", lambda: SimpleNamespace(acquire=acquire))
            with pytest.raises(RuntimeError, match="mock"):
                await repo.upsert_push_subscription(ids[0], url, "key", "auth")
        assert await pool.fetchval(
            "SELECT count(*) FROM push_subscriptions WHERE user_id=$1", ids[0]) == 9
        assert await repo.find_subscription_for_user(ids[0], url) is None
        # Acquire on another connection while the first connection is still
        # checked out: transaction locks must already have been released.
        await asyncio.wait_for(repo.upsert_push_subscription(ids[0], url, "key", "auth"), 3)
    assert await count(ids[0]) == 10


async def test_redis_outage_blocks_http_registration_but_preserves_repository_and_cleanup(accounts, monkeypatch):
    ids, clients = accounts

    def unavailable(*args, **kwargs):
        raise ConnectionError("unavailable")

    monkeypatch.setattr(limiter._storage, "lua_incr_expire", unavailable)
    urls = []
    for _ in range(10):
        url = endpoint()
        urls.append(url)
        await repo.upsert_push_subscription(ids[0], url, "key", "auth")
    assert (await register(clients[0], endpoint())).status_code == 503
    with pytest.raises(repo.PushDeviceLimitReached):
        await repo.upsert_push_subscription(ids[0], endpoint(), "key", "auth")
    assert (await clients[0].request("DELETE", "/api/push/subscribe",
                                    json={"endpoint": urls[0]})).status_code == 204
    await repo.upsert_push_subscription(ids[0], endpoint(), "key", "auth")
    assert await count(ids[0]) == 10
