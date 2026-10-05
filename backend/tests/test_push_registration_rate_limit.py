"""Registration admission uses real local Redis; repository work is mocked."""

import asyncio
import math
import time
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from limits import parse
from redis.exceptions import ConnectionError

from app.config import settings
from app.main import create_app
from app.rate_limit import create_limiter, limiter, request_identity
from app.repositories import push_subscriptions as repo
from app.security import create_token

PATH = "/api/push/subscribe"
PAYLOAD = {"endpoint": "https://updates.push.services.mozilla.com/wpush/v2/test",
           "keys": {"p256dh": "key", "auth": "auth"}}
ITEM = parse("20/minute")


def client(uid=None, app=None, ip="192.0.2.1"):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app or create_app(), client=(ip, 123)),
        base_url="https://test",
        cookies={settings.auth_cookie_name: create_token(str(uid))} if uid else {},
    )


def identifiers(uid):
    return limiter._key_prefix, f"user:{uid}", PATH


@pytest_asyncio.fixture
async def registration(monkeypatch):
    uid = uuid4()
    store = AsyncMock()
    monkeypatch.setattr(repo, "upsert_push_subscription", store)
    async with client(uid) as api:
        yield uid, api, store


async def test_twenty_refreshes_then_rejection_retry_after_and_expiry(registration):
    uid, api, store = registration
    for _ in range(20):
        assert (await api.post(PATH, json=PAYLOAD)).status_code == 201
    reset, remaining = limiter.limiter.get_window_stats(ITEM, *identifiers(uid))
    assert remaining == 0
    response = await api.post(PATH, json=PAYLOAD)
    assert response.status_code == 429
    assert abs(int(response.headers["retry-after"]) - max(1, math.ceil(reset - time.time()))) <= 1
    assert store.await_count == 20
    # Expire the actual Redis key, without waiting a minute or changing policy.
    key = limiter._storage.prefixed_key(ITEM.key_for(*identifiers(uid)))
    limiter._storage.storage.pexpire(key, 1)
    await asyncio.sleep(0.02)
    assert (await api.post(PATH, json=PAYLOAD)).status_code == 201
    assert store.await_count == 21


async def test_account_identity_across_ips_and_independent_users(registration):
    uid, api, store = registration
    for _ in range(20):
        assert (await api.post(PATH, json=PAYLOAD)).status_code == 201
    async with client(uid, ip="198.51.100.2") as other_ip:
        assert (await other_ip.post(PATH, json=PAYLOAD)).status_code == 429
    async with client(uuid4()) as other_user:
        assert (await other_user.post(PATH, json=PAYLOAD)).status_code == 201
    assert store.await_count == 21


async def test_concurrent_apps_and_restarted_app_share_quota(registration):
    uid, api, store = registration
    async with client(uid) as second:
        responses = await asyncio.gather(*[
            (api if i % 2 else second).post(PATH, json=PAYLOAD) for i in range(25)])
    assert sum(r.status_code == 201 for r in responses) == 20
    assert sum(r.status_code == 429 for r in responses) == 5
    assert store.await_count == 20
    async with client(uid) as restarted:
        assert (await restarted.post(PATH, json=PAYLOAD)).status_code == 429
    # Independent limiter objects/connections see the same route counter too.
    other = create_limiter("test")
    assert not await asyncio.to_thread(other.limiter.hit, ITEM, *identifiers(uid))
    assert store.await_count == 20


async def test_redis_outage_fails_closed_before_repository(registration, monkeypatch):
    _uid, api, store = registration

    def unavailable(*args, **kwargs):
        raise ConnectionError("test outage")

    monkeypatch.setattr(limiter._storage, "lua_incr_expire", unavailable)
    for _ in range(2):
        response = await api.post(PATH, json=PAYLOAD)
        assert response.status_code == 503
        assert response.headers["retry-after"] == "5"
    assert not limiter._in_memory_fallback_enabled
    assert limiter._fallback_limiter is None
    store.assert_not_awaited()


@pytest.mark.parametrize("kind,status", [("unauthenticated", 401), ("oversized", 413),
                                         ("invalid", 422)])
async def test_existing_rejections_before_rate_check(registration, monkeypatch, kind, status):
    uid, api, store = registration

    def unexpected(*args, **kwargs):
        raise AssertionError("rate check must not run")

    monkeypatch.setattr(limiter._storage, "lua_incr_expire", unexpected)
    if kind == "unauthenticated":
        async with client() as unauthenticated:
            response = await unauthenticated.post(PATH, json=PAYLOAD)
    elif kind == "oversized":
        response = await api.post(PATH, content=b" " * 16385)
    else:
        response = await api.post(PATH, json={"endpoint": "https://evil.example/push",
                                             "keys": PAYLOAD["keys"]})
    assert response.status_code == status
    store.assert_not_awaited()
    assert request_identity(api_request(uid)) == f"user:{uid}"


def api_request(uid):
    from starlette.requests import Request
    return Request({"type": "http", "path": PATH, "scheme": "https", "server": ("test", 443),
                    "query_string": b"", "client": ("192.0.2.1", 1),
                    "headers": [(b"cookie", f"{settings.auth_cookie_name}={create_token(str(uid))}".encode())]})


@pytest.mark.parametrize("error,detail", [
    (repo.PushDeviceLimitReached, "Push subscription device limit reached"),
    (repo.PushOwnershipConflict, "still linked to another account"),
])
async def test_repository_rejections_consume_quota(registration, error, detail):
    _uid, api, store = registration
    store.side_effect = error("test")
    for _ in range(20):
        response = await api.post(PATH, json=PAYLOAD)
        assert response.status_code == 409
        assert detail in response.json()["detail"]
    assert (await api.post(PATH, json=PAYLOAD)).status_code == 429
    assert store.await_count == 20


async def test_other_route_counters_do_not_block_registration(registration):
    uid, api, store = registration
    for path, limit, identity in [
        ("/api/push/test", "5/minute", f"user:{uid}"),
        ("/api/teams/search", "120/minute", f"user:{uid}"),
        ("/api/auth/signup", "20/15minutes", "peer:192.0.2.1"),
        ("/api/auth/login", "20/15minutes", "peer:192.0.2.1"),
    ]:
        item = parse(limit)
        for _ in range(item.amount):
            assert limiter.limiter.hit(item, limiter._key_prefix, identity, path)
    assert (await api.post(PATH, json=PAYLOAD)).status_code == 201
    store.assert_awaited_once()


def test_registration_namespace_isolation(monkeypatch):
    namespace = f"test:{uuid4()}"
    monkeypatch.setenv("VERCEL_ENV", "production")
    production = create_limiter(namespace)
    monkeypatch.setenv("VERCEL_ENV", "preview")
    preview = create_limiter(namespace)
    testing = create_limiter(namespace + ":other")
    for _ in range(20):
        assert production.limiter.hit(ITEM, production._key_prefix, "user:A", PATH)
    assert not production.limiter.hit(ITEM, production._key_prefix, "user:A", PATH)
    for instance in [preview, testing]:
        assert instance.limiter.hit(ITEM, instance._key_prefix, "user:A", PATH)
