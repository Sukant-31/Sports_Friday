"""Real local Redis regression tests; no upstream or push network delivery."""

import asyncio
from uuid import uuid4

import httpx
import pytest
from limits import parse
from redis.exceptions import ConnectionError
from starlette.requests import Request

from app.config import settings
from app.main import create_app
from app.rate_limit import create_limiter, limiter, request_identity
from app.security import create_token


def pair():
    namespace = f"test:{uuid4()}"
    return create_limiter(namespace), create_limiter(namespace)


def test_implicit_slowapi_options_cannot_enable_fallback(monkeypatch):
    monkeypatch.setenv("RATELIMIT_IN_MEMORY_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("RATELIMIT_SWALLOW_ERRORS", "true")
    monkeypatch.setenv("RATELIMIT_ENABLED", "false")
    monkeypatch.setenv("RATELIMIT_DEFAULT", "1/minute")
    a, _ = pair()
    assert a.enabled
    assert not a._in_memory_fallback_enabled
    assert not a._swallow_errors
    assert not a._default_limits
    assert a._fallback_limiter is None


def test_memory_storage_is_not_allowed():
    with pytest.raises(ValueError, match="requires Redis"):
        create_limiter("test", storage_uri="memory://")


def test_shared_and_restart():
    a, b = pair()
    item = parse("2/minute")
    args = (a._key_prefix, "user", "endpoint")
    assert a.limiter.hit(item, *args)
    assert b.limiter.hit(item, *args)
    environment = a._key_prefix.removeprefix("sports-friday:http-rate-limit:").rsplit(":", 1)[0]
    restarted = create_limiter(environment)
    assert not restarted.limiter.hit(item, *args)


async def test_atomic_concurrency():
    a, b = pair()
    item = parse("7/minute")
    args = (a._key_prefix, "user", "endpoint")
    results = await asyncio.gather(
        *[asyncio.to_thread((a if i % 2 else b).limiter.hit, item, *args) for i in range(40)]
    )
    assert sum(results) == 7


async def test_expiry():
    a, _ = pair()
    item = parse("1/second")
    args = (a._key_prefix, "user", "endpoint")
    assert a.limiter.hit(item, *args)
    await asyncio.sleep(0.6)
    assert not a.limiter.hit(item, *args)
    await asyncio.sleep(0.5)
    assert a.limiter.hit(item, *args)


def test_users_endpoints_and_environments():
    a, _ = pair()
    item = parse("1/minute")
    assert a.limiter.hit(item, a._key_prefix, "A", "login")
    assert not a.limiter.hit(item, a._key_prefix, "A", "login")
    assert a.limiter.hit(item, a._key_prefix, "B", "login")
    assert a.limiter.hit(item, a._key_prefix, "A", "signup")
    assert a.limiter.hit(item, a._key_prefix + ":another-env", "A", "login")


def identity(path, peer, cookie=None, headers=()):
    headers = list(headers)
    if cookie:
        headers.append((b"cookie", f"{settings.auth_cookie_name}={cookie}".encode()))
    return request_identity(
        Request(
            {
                "type": "http",
                "path": path,
                "headers": headers,
                "client": (peer, 123),
                "scheme": "https",
                "server": ("backend", 443),
                "query_string": b"",
            }
        )
    )


def test_forwarding_and_proxy_identity():
    forged = [(b"x-forwarded-for", b"198.51.100.9"), (b"x-vercel-forwarded-for", b"198.51.100.8")]
    assert identity("/api/auth/login", "peer", headers=forged) == "peer:peer"
    a, b = create_token(str(uuid4())), create_token(str(uuid4()))
    assert identity("/api/teams/search", "direct", a) == identity("/api/teams/search", "proxy", a)
    assert identity("/api/push/test", "direct", a) != identity("/api/push/test", "direct", b)
    assert identity("/api/auth/signup", "peer", a) == identity("/api/auth/signup", "peer", b)
    assert identity("/api/teams/search", "peer", "forged") == "peer:peer"


def test_vercel_edge_identity(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    # Vercel overwrites this header at its edge; raw X-Forwarded-For is ignored.
    authoritative = [(b"x-vercel-forwarded-for", b"198.51.100.10")]
    for forged in [b"198.51.100.1", b"198.51.100.2, 198.51.100.3"]:
        headers = authoritative + [(b"x-forwarded-for", forged)]
        assert identity("/api/auth/login", "internal", headers=headers) == "peer:198.51.100.10"
    assert (
        identity(
            "/api/auth/login",
            "internal",
            headers=[(b"x-vercel-forwarded-for", b"198.51.100.1, 198.51.100.2")],
        )
        == "peer:internal"
    )
    monkeypatch.setenv("VERCEL", "0")
    assert identity("/api/auth/login", "direct", headers=authoritative) == "peer:direct"


def test_deployment_environment_isolation(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    a = create_limiter("test-" + uuid4().hex)
    monkeypatch.setenv("VERCEL_ENV", "preview")
    b = create_limiter(
        a._key_prefix.removeprefix("sports-friday:http-rate-limit:").rsplit(":", 1)[0]
    )
    item = parse("1/minute")
    assert a.limiter.hit(item, a._key_prefix, "user", "login")
    assert not a.limiter.hit(item, a._key_prefix, "user", "login")
    assert b.limiter.hit(item, b._key_prefix, "user", "login")


@pytest.mark.parametrize(
    "path,limit",
    [
        ("/api/auth/login", "20/15minutes"),
        ("/api/auth/signup", "20/15minutes"),
        ("/api/teams/search", "120/minute"),
        ("/api/push/test", "5/minute"),
    ],
)
async def test_rejected_handlers_and_retry_after(monkeypatch, path, limit):
    from unittest.mock import AsyncMock, Mock

    from app.deps import get_current_user_id
    from app.routers import push
    from app.services import auth_service, team_service

    app = create_app()
    uid = uuid4()
    app.dependency_overrides[get_current_user_id] = lambda: uid
    hashing = Mock(side_effect=AssertionError("hashing must not run"))
    monkeypatch.setattr(auth_service, "hash_password", hashing)
    monkeypatch.setattr(auth_service, "verify_password", hashing)
    search = AsyncMock(side_effect=AssertionError("provider must not run"))
    monkeypatch.setattr(team_service, "search_teams", search)
    delivery = AsyncMock(side_effect=AssertionError("push must not run"))
    monkeypatch.setattr(push, "send_push", delivery)
    key = (
        "user:" + str(uid) if path in {"/api/teams/search", "/api/push/test"} else "peer:127.0.0.1"
    )
    item = parse(limit)
    for _ in range(item.amount):
        assert limiter.limiter.hit(item, limiter._key_prefix, key, path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies={settings.auth_cookie_name: create_token(str(uid))},
    ) as client:
        body = (
            {"endpoint": "https://updates.push.services.mozilla.com/wpush/v2/test"}
            if path.endswith("/test")
            else {"email": "test@example.com", "password": "valid-password"}
        )
        response = (
            await client.get(path, params={"q": "Barcelona"})
            if path.endswith("/search")
            else await client.post(path, json=body)
        )
    assert response.status_code == 429, response.text
    assert 1 <= int(response.headers["retry-after"]) <= item.get_expiry() + 1
    hashing.assert_not_called()
    search.assert_not_called()
    delivery.assert_not_called()


async def test_outage_and_accessible_routes(monkeypatch):
    from unittest.mock import AsyncMock

    from app import main
    from app.deps import get_current_user_id
    from app.repositories import matches, muted_matches, push_subscriptions
    from app.services import subscription_service

    app = create_app()
    app.dependency_overrides[get_current_user_id] = lambda: uuid4()
    monkeypatch.setattr(
        limiter._storage,
        "lua_incr_expire",
        lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("unavailable")),
    )
    monkeypatch.setattr(push_subscriptions, "upsert_push_subscription", AsyncMock())
    monkeypatch.setattr(push_subscriptions, "delete_push_subscription_by_endpoint", AsyncMock())
    monkeypatch.setattr(matches, "find_live_matches_for_user", AsyncMock(return_value=[]))
    monkeypatch.setattr(matches, "find_recent_events_for_matches", AsyncMock(return_value=[]))
    monkeypatch.setattr(muted_matches, "list_muted_match_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(subscription_service, "list_subscriptions", AsyncMock(return_value=[]))
    cron = AsyncMock(return_value={"ok": True, "status": "idle"})
    monkeypatch.setattr(main, "run_once", cron)
    endpoint = "https://updates.push.services.mozilla.com/wpush/v2/test"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for _ in range(2):
            response = await client.post(
                "/api/auth/login", json={"email": "test@example.com", "password": "valid-password"}
            )
            assert response.status_code == 503
        assert not limiter._in_memory_fallback_enabled
        assert (await client.post("/api/auth/logout")).status_code == 200
        assert (
            await client.post(
                "/api/push/subscribe",
                json={"endpoint": endpoint, "keys": {"p256dh": "key", "auth": "auth"}},
            )
        ).status_code == 503
        assert (
            await client.request("DELETE", "/api/push/subscribe", json={"endpoint": endpoint})
        ).status_code == 204
        assert (await client.get("/health")).status_code == 200
        for _ in range(6):
            assert (await client.get("/api/matches/live")).status_code == 200
            assert (await client.get("/api/subscriptions")).status_code == 200
        response = await client.get(
            "/api/cron/poll", headers={"Authorization": f"Bearer {settings.cron_secret}"}
        )
        assert response.status_code == 200
    cron.assert_awaited_once()


@pytest.mark.parametrize("path", ["/api/auth/signup", "/api/teams/search", "/api/push/test"])
async def test_all_protected_operations_fail_closed(monkeypatch, path):
    from unittest.mock import AsyncMock

    from app.deps import get_current_user_id
    from app.routers import push
    from app.services import auth_service, team_service

    app = create_app()
    app.dependency_overrides[get_current_user_id] = lambda: uuid4()
    denied = AsyncMock(side_effect=AssertionError("handler must not run"))
    monkeypatch.setattr(auth_service, "signup", denied)
    monkeypatch.setattr(team_service, "search_teams", denied)
    monkeypatch.setattr(push, "send_push", denied)
    monkeypatch.setattr(
        limiter._storage,
        "lua_incr_expire",
        lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("unavailable")),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = (
            {"endpoint": "https://updates.push.services.mozilla.com/wpush/v2/test"}
            if path.endswith("/test")
            else {"email": "test@example.com", "password": "valid-password"}
        )
        response = (
            await client.get(path)
            if path.endswith("/search")
            else await client.post(path, json=body)
        )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    denied.assert_not_awaited()


async def test_rejected_follow_never_calls_provider(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services import subscription_service

    create = AsyncMock(side_effect=AssertionError("unauthenticated follow must not run"))
    monkeypatch.setattr(subscription_service, "create", create)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post("/api/subscriptions", json={"teamId": str(uuid4())})
    assert response.status_code == 401
    create.assert_not_awaited()
