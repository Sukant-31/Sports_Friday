"""Registration byte boundaries, streaming enforcement, and ownership compatibility."""

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from app import db
from app.deps import get_current_user_id
from app.main import create_app
from app.push_registration_limits import BODY_BYTES, ENDPOINT_BYTES, KEY_BYTES
from app.rate_limit import limiter
from app.repositories import push_subscriptions, users
from app.routers import push

FIREFOX = "https://updates.push.services.mozilla.com/wpush/v2/token"
FCM = "https://fcm.googleapis.com/fcm/send/token"
APPLE = "https://web.push.apple.com/token"


def registration(endpoint=FIREFOX, **keys):
    return {"endpoint": endpoint, "keys": {"p256dh": "key", "auth": "auth", **keys}}


def encode(payload):
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def padded_body(size, endpoint=FIREFOX):
    body = encode(registration(endpoint))
    return body + b" " * (size - len(body))


@pytest_asyncio.fixture
async def api(monkeypatch):
    app = create_app()
    user = uuid4()
    app.dependency_overrides[get_current_user_id] = lambda: user
    store = AsyncMock()
    delete = AsyncMock()
    monkeypatch.setattr(push.push_repo, "upsert_push_subscription", store)
    monkeypatch.setattr(push.push_repo, "delete_push_subscription_by_endpoint", delete)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="https://test") as client:
        yield client, app, user, store, delete


@pytest.mark.parametrize("endpoint", [FIREFOX, FCM, APPLE])
async def test_exact_body_boundary_and_supported_provider(api, endpoint):
    client, _app, user, store, _delete = api
    body = padded_body(BODY_BYTES, endpoint)
    assert len(body) == BODY_BYTES
    response = await client.post("/api/push/subscribe", content=body,
                                 headers={"Content-Type": "application/json"})
    assert response.status_code == 201
    store.assert_awaited_once_with(user, endpoint, "key", "auth")


@pytest.mark.parametrize("declared", [None, "0", "1", str(BODY_BYTES), "invalid"])
async def test_streamed_one_byte_over_rejected_before_storage(api, declared):
    client, _app, _user, store, _delete = api
    body = padded_body(BODY_BYTES + 1)

    async def chunks():
        yield body[:BODY_BYTES]
        yield body[BODY_BYTES:]

    headers = {"Content-Type": "application/json"}
    if declared is not None:
        headers["Content-Length"] = declared
    response = await client.post("/api/push/subscribe", content=chunks(), headers=headers)
    assert response.status_code == 413
    assert response.json() == {
        "detail": f"Push registration body must be at most {BODY_BYTES} bytes"}
    assert response.headers["x-content-type-options"] == "nosniff"
    store.assert_not_awaited()


@pytest.mark.parametrize("declared", [str(BODY_BYTES + 1), "9" * 5000])
async def test_oversized_declared_length_rejected_without_reading_body(api, declared):
    client, _app, _user, store, _delete = api

    async def unread():
        raise AssertionError("declared oversized request must not be read")
        yield b""  # pragma: no cover

    response = await client.post("/api/push/subscribe", content=unread(),
                                 headers={"Content-Length": declared})
    assert response.status_code == 413
    store.assert_not_awaited()


async def test_missing_length_and_understated_length_accept_valid_actual_body(api):
    client, _app, _user, store, _delete = api
    for declared in [None, "1"]:
        async def chunks():
            yield padded_body(BODY_BYTES)
        headers = {"Content-Type": "application/json"}
        if declared:
            headers["Content-Length"] = declared
        assert (await client.post("/api/push/subscribe", content=chunks(),
                                  headers=headers)).status_code == 201
    assert store.await_count == 2


@pytest.mark.parametrize("over", [0, 1])
async def test_endpoint_byte_boundary(api, over):
    client, _app, _user, store, _delete = api
    endpoint = FIREFOX + "x" * (ENDPOINT_BYTES + over - len(FIREFOX))
    response = await client.post("/api/push/subscribe", json=registration(endpoint))
    assert response.status_code == (422 if over else 201)
    if over:
        assert response.json()["detail"][0]["loc"] == ["body", "endpoint"]
        store.assert_not_awaited()
    else:
        assert store.await_args.args[1] == endpoint


@pytest.mark.parametrize("field", ["p256dh", "auth"])
@pytest.mark.parametrize("multibyte", [False, True])
@pytest.mark.parametrize("over", [0, 1])
async def test_key_utf8_byte_boundaries(api, field, multibyte, over):
    client, _app, _user, store, _delete = api
    value = ("é" * (KEY_BYTES // 2) if multibyte else "x" * KEY_BYTES) + "x" * over
    assert len(value.encode("utf-8")) == KEY_BYTES + over
    response = await client.post("/api/push/subscribe", content=encode(registration(**{field: value})),
                                 headers={"Content-Type": "application/json"})
    assert response.status_code == (422 if over else 201)
    if over:
        assert response.json()["detail"][0]["loc"] == ["body", "keys", field]
        store.assert_not_awaited()
    else:
        assert value in store.await_args.args


async def test_total_body_counts_utf8_bytes(api):
    client, _app, _user, store, _delete = api
    payload = registration()
    payload["ignored"] = "é" * (BODY_BYTES // 2)
    body = encode(payload)
    assert len(body) > BODY_BYTES
    assert len(body.decode("utf-8")) < BODY_BYTES
    response = await client.post("/api/push/subscribe", content=body,
                                 headers={"Content-Type": "application/json", "Content-Length": "1"})
    assert response.status_code == 413
    store.assert_not_awaited()


async def test_legacy_unsubscribe_is_not_subject_to_registration_limits(api):
    client, _app, user, store, delete = api
    endpoint = "http://localhost/legacy/" + "é" * BODY_BYTES
    response = await client.request("DELETE", "/api/push/subscribe", json={"endpoint": endpoint})
    assert response.status_code == 204
    delete.assert_awaited_once_with(user, endpoint)
    store.assert_not_awaited()


async def test_trailing_slash_cannot_bypass_body_limit(api):
    client, _app, _user, store, _delete = api
    response = await client.post("/api/push/subscribe/", content=padded_body(BODY_BYTES + 1))
    assert response.status_code == 413
    store.assert_not_awaited()


async def test_redis_outage_does_not_block_boundary_registration_or_legacy_cleanup(api, monkeypatch):
    client, _app, _user, store, delete = api

    def unavailable(*args, **kwargs):
        raise ConnectionError("unavailable")

    monkeypatch.setattr(limiter._storage, "lua_incr_expire", unavailable)
    response = await client.post("/api/push/subscribe", content=padded_body(BODY_BYTES),
                                 headers={"Content-Type": "application/json"})
    assert response.status_code == 201
    store.assert_awaited_once()
    response = await client.request("DELETE", "/api/push/subscribe",
                                    json={"endpoint": "http://localhost/legacy"})
    assert response.status_code == 204
    delete.assert_awaited_once()


@pytest_asyncio.fixture
async def accounts():
    await db.connect()
    ids = []
    try:
        for _ in range(2):
            row = await users.create_user(f"limits-{uuid4().hex}@example.com", "test-hash")
            ids.append(row["id"])
        yield ids
    finally:
        await db.execute("DELETE FROM users WHERE id=ANY($1::uuid[])", ids)
        await db.disconnect()


async def test_concurrent_boundary_registration_update_and_account_isolation(accounts):
    endpoint = FIREFOX + uuid4().hex
    endpoint += "x" * (ENDPOINT_BYTES - len(endpoint))
    clients = []
    try:
        for uid in accounts:
            app = create_app()
            app.dependency_overrides[get_current_user_id] = lambda uid=uid: uid
            clients.append(httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                              base_url="https://test"))
        responses = await asyncio.gather(*[
            clients[0].post("/api/push/subscribe", content=padded_body(BODY_BYTES, endpoint),
                            headers={"Content-Type": "application/json"}) for _ in range(4)])
        assert [r.status_code for r in responses] == [201] * 4
        assert await db.pool().fetchval(
            "SELECT count(*) FROM push_subscriptions WHERE endpoint=$1", endpoint) == 1
        first = await push_subscriptions.find_subscription_for_user(accounts[0], endpoint)
        update = await clients[0].post("/api/push/subscribe",
                                       json=registration(endpoint, p256dh="updated"))
        assert update.status_code == 201
        updated = await push_subscriptions.find_subscription_for_user(accounts[0], endpoint)
        assert updated["push_id"] == first["push_id"]
        assert updated["p256dh"] == "updated"
        conflict = await clients[1].post("/api/push/subscribe", json=registration(endpoint))
        assert conflict.status_code == 409
        assert await push_subscriptions.find_subscription_for_user(accounts[1], endpoint) is None
        assert (await clients[1].request("DELETE", "/api/push/subscribe",
                                         json={"endpoint": endpoint})).status_code == 204
        assert await push_subscriptions.find_subscription_for_user(accounts[0], endpoint) is not None
    finally:
        for client in clients:
            await client.aclose()
