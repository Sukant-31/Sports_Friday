"""Destination boundaries and the real push transport, with all HTTP mocked."""

import base64
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pywebpush import WebPushException
from requests import Response
from requests.adapters import HTTPAdapter

from app.config import settings
from app.deps import get_current_user_id
from app.main import create_app
from app.push_destination import validate_push_destination
from app.routers import push
from app.workers import web_push

FIREFOX = "https://updates.push.services.mozilla.com/wpush/v2/test-token"
ACCEPTED = [
    FIREFOX,
    "https://updates.push.services.mozilla.com:443/wpush/v2/test-token",
    "https://UPDATES.PUSH.SERVICES.MOZILLA.COM/wpush/v2/test-token",
    "https://fcm.googleapis.com/fcm/send/test-token",
    "https://fcm.googleapis.com/wp/test-token?opaque=a%2Fb",
    "https://web.push.apple.com/Qtest-token",
    "https://regional.push.apple.com/Qtest-token",
    "https://a.b.push.apple.com/Qtest-token?opaque=a%2Fb",
    "https://WEB.PUSH.APPLE.COM:443/Qtest-token",
]
REJECTED = [
    "", "not-a-url", "//fcm.googleapis.com/wp/token",
    "http://updates.push.services.mozilla.com/wpush/v2/token",
    "https://localhost/push", "https://localhost.localdomain/push",
    "https://127.0.0.1/push", "https://10.0.0.1/push",
    "https://172.16.0.1/push", "https://192.168.0.1/push",
    "https://169.254.169.254/push", "https://8.8.8.8/push",
    "https://[::1]/push", "https://[fc00::1]/push",
    "https://[2001:4860:4860::8888]/push",
    "https://user:password@fcm.googleapis.com/wp/token",
    "https://@fcm.googleapis.com/wp/token",
    "https://fcm.googleapis.com:80/wp/token",
    "https://fcm.googleapis.com:444/wp/token",
    "https://fcm.googleapis.com:invalid/wp/token",
    "https://fcm.googleapis.com:99999/wp/token",
    "https://fcm.googleapis.com:/wp/token",
    "https://fcm.googleapis.com:0443/wp/token",
    "https://fcm.googleapis.com.evil.example/wp/token",
    "https://evil-fcm.googleapis.com/wp/token",
    "https://evil.example/fcm.googleapis.com/wp/token",
    "https://updates.push.services.mozilla.com.evil.example/push",
    "https://web.push.apple.com.evil.example/push",
    "https://push.apple.com.attacker.example/push",
    "https://evilpush.apple.com/push",
    "https://push.apple.com/push",
    "https://unrelated.apple.com/push",
    "https://.push.apple.com/push",
    "https://web..push.apple.com/push",
    "https://-web.push.apple.com/push",
    "https://web-.push.apple.com/push",
    "https://web_push.push.apple.com/push",
    "https://web.push.apple.com./push",
    "https://%77eb.push.apple.com/push",
    "https://" + "a" * 64 + ".push.apple.com/push",
    "https://" + ("a" * 63 + ".") * 4 + "push.apple.com/push",
    "http://web.push.apple.com/push",
    "https://web.push.apple.com:444/push",
    "https://web.push.apple.com:0443/push",
    "https://user:password@web.push.apple.com/push",
    "https://fcm.googleapis.com./wp/token",
    "https://%66cm.googleapis.com/wp/token",
    "https://fcm.googleapis.com\\@evil.example/push",
    " https://fcm.googleapis.com/wp/token",
    "https://fcm.googleapis.com/wp/to\nken",
    "https://fcm.googleapis.com/wp/to\x00ken",
    "https://fcm.googleapis.com/wp/token#fragment",
    "https://fcm.googleapis.com/wp/token#",
    "https://fcm.googleapis.com/wp/%bad%token",
    "https://fcm.googleapis.com/wp/токен",
    "https://[fcm.googleapis.com/wp/token",
    "https://fcm.googleapis.com", "https://fcm.googleapis.com/",
]


@pytest.mark.parametrize("endpoint", ACCEPTED)
def test_accepts_approved_provider_and_preserves_opaque_url(endpoint):
    assert validate_push_destination(endpoint) == endpoint


@pytest.mark.parametrize("endpoint", REJECTED)
def test_rejects_unsafe_or_ambiguous_destination(endpoint):
    with pytest.raises(ValueError, match="approved Web Push provider"):
        validate_push_destination(endpoint)


@pytest_asyncio.fixture
async def push_api(monkeypatch):
    app = create_app()
    user = uuid4()
    app.dependency_overrides[get_current_user_id] = lambda: user
    monkeypatch.setattr(push.limiter, "enabled", False)
    store, lookup, delete, send = (AsyncMock() for _ in range(4))
    monkeypatch.setattr(push.push_repo, "upsert_push_subscription", store)
    monkeypatch.setattr(push.push_repo, "find_subscription_for_user", lookup)
    monkeypatch.setattr(push.push_repo, "delete_push_subscription_by_endpoint", delete)
    monkeypatch.setattr(push, "send_push", send)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="https://test") as client:
        yield client, app, user, store, lookup, delete, send


@pytest.mark.parametrize("endpoint", REJECTED)
async def test_registration_and_test_reject_before_storage_lookup_or_send(push_api, endpoint):
    client, _app, _user, store, lookup, _delete, send = push_api
    registration = await client.post("/api/push/subscribe", json={
        "endpoint": endpoint, "keys": {"p256dh": "test-key", "auth": "test-auth"}})
    test = await client.post("/api/push/test", json={"endpoint": endpoint})
    assert registration.status_code == test.status_code == 422
    store.assert_not_awaited()
    lookup.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.parametrize("endpoint", ACCEPTED)
async def test_registration_preserves_user_destination_and_keys(push_api, endpoint):
    client, _app, user, store, _lookup, _delete, _send = push_api
    response = await client.post("/api/push/subscribe", json={
        "endpoint": endpoint, "keys": {"p256dh": "test-key", "auth": "test-auth"}})
    assert response.status_code == 201
    store.assert_awaited_once_with(user, endpoint, "test-key", "test-auth")


async def test_registration_still_requires_authentication(push_api):
    client, app, _user, store, _lookup, _delete, _send = push_api
    app.dependency_overrides.clear()
    response = await client.post("/api/push/subscribe", json={
        "endpoint": FIREFOX, "keys": {"p256dh": "test-key", "auth": "test-auth"}})
    assert response.status_code == 401
    store.assert_not_awaited()


async def test_unsubscribe_can_remove_unsafe_legacy_row_and_remains_user_scoped(push_api):
    client, _app, user, _store, _lookup, delete, _send = push_api
    response = await client.request("DELETE", "/api/push/subscribe",
                                    json={"endpoint": "http://localhost/legacy"})
    assert response.status_code == 204
    delete.assert_awaited_once_with(user, "http://localhost/legacy")


@pytest.fixture
def transport(monkeypatch):
    """Exercise real encryption/signing; replace the adapter's actual HTTP I/O."""
    monkeypatch.setattr(settings, "push_transport", "webpush")
    signing_key = ec.generate_private_key(ec.SECP256R1())
    raw_private = signing_key.private_numbers().private_value.to_bytes(32, "big")
    monkeypatch.setattr(settings, "vapid_private_key", base64.urlsafe_b64encode(raw_private).decode())
    monkeypatch.setattr(settings, "vapid_subject", "mailto:audit@example.com")
    receiver = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    target = {
        "push_id": uuid4(), "endpoint": FIREFOX,
        "p256dh": base64.urlsafe_b64encode(receiver).decode(),
        "auth": base64.urlsafe_b64encode(bytes(range(16))).decode(),
    }
    response = Response()
    response.status_code = 201
    response.reason = "Mock response"
    response._content = b""
    def respond(request, **_kwargs):
        response.request = request
        return response
    network = Mock(side_effect=respond)
    monkeypatch.setattr(HTTPAdapter, "send", network)
    delete = AsyncMock()
    monkeypatch.setattr(web_push.push_repo, "delete_push_subscription_by_id", delete)
    return target, response, network, delete


@pytest.mark.parametrize("endpoint", REJECTED)
async def test_unsafe_legacy_destination_never_reaches_http_transport(transport, endpoint):
    target, _response, network, delete = transport
    target["endpoint"] = endpoint
    with pytest.raises(ValueError):
        await web_push.send_push(target, {"title": "Test"})
    network.assert_not_called()
    delete.assert_not_awaited()


@pytest.mark.parametrize("endpoint", ACCEPTED)
async def test_real_transport_sends_approved_encrypted_push(transport, endpoint):
    target, _response, network, delete = transport
    target["endpoint"] = endpoint
    await web_push.send_push(target, {"title": "Test"})
    network.assert_called_once()
    request = network.call_args.args[0]
    assert request.url.lower() == endpoint.lower()
    assert request.method == "POST"
    assert request.headers["Content-Encoding"] == "aes128gcm"
    assert request.headers["Authorization"].startswith("vapid ")
    assert network.call_args.kwargs["timeout"] == 10
    delete.assert_not_awaited()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("location", ["http://127.0.0.1/internal",
                                      "https://fcm.googleapis.com/wp/redirected"])
async def test_redirects_are_refused_without_second_request(transport, status, location):
    target, response, network, delete = transport
    response.status_code = status
    response.headers["Location"] = location
    with pytest.raises(WebPushException, match="redirect refused"):
        await web_push.send_push(target, {"title": "Test"})
    network.assert_called_once()
    delete.assert_not_awaited()


@pytest.mark.parametrize("status", [404, 410])
async def test_expired_subscriptions_are_still_pruned(transport, status):
    target, response, network, delete = transport
    response.status_code = status
    await web_push.send_push(target, {"title": "Test"})
    network.assert_called_once()
    delete.assert_awaited_once_with(target["push_id"])


async def test_provider_failure_still_propagates_without_pruning(transport):
    target, response, network, delete = transport
    response.status_code = 503
    with pytest.raises(WebPushException):
        await web_push.send_push(target, {"title": "Test"})
    network.assert_called_once()
    delete.assert_not_awaited()


def test_transport_rechecks_actual_destination_before_network(transport):
    _target, _response, network, _delete = transport
    with web_push._PushSession() as session, pytest.raises(ValueError):
        session.post("https://evil.example/push")
    network.assert_not_called()


async def test_console_demo_transport_remains_network_free(transport, monkeypatch):
    target, _response, network, delete = transport
    monkeypatch.setattr(settings, "push_transport", "console")
    target["endpoint"] = "console://demo"
    await web_push.send_push(target, {"title": "Console demo"})
    network.assert_not_called()
    delete.assert_not_awaited()
