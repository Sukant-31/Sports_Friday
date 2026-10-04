"""Read-only HTTP smoke coverage for response headers and unchanged CORS."""
from unittest.mock import Mock

import httpx
import pytest
from redis.exceptions import ConnectionError

from app.config import settings
from app.main import create_app
from app.rate_limit import limiter
from app.security_headers import HEADERS


@pytest.mark.parametrize('path,status', [('/health', 200), ('/api/auth/me', 401),
                                        ('/does-not-exist', 404)])
async def test_headers_on_success_and_errors(path, status):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                base_url='http://test') as client:
        response = await client.get(path)
    assert response.status_code == status
    for name, value in HEADERS.items():
        assert response.headers[name] == value
    assert 'content-security-policy' not in response.headers


async def test_validation_and_storage_errors_keep_headers(monkeypatch):
    monkeypatch.setattr(limiter._storage, 'lua_incr_expire',
                        Mock(side_effect=ConnectionError('Synthetic outage')))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                base_url='http://test') as client:
        invalid = await client.post('/api/auth/signup', json={})
        outage = await client.post('/api/auth/login', json={
            'email': 'header-test@example.com', 'password': 'valid-password'})
    assert invalid.status_code == 422
    assert outage.status_code == 503
    assert outage.headers['retry-after'] == '5'
    for response in (invalid, outage):
        for name, value in HEADERS.items():
            assert response.headers[name] == value


async def test_cors_preflight_is_unchanged():
    origin = settings.cors_origins[0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                base_url='http://test') as client:
        response = await client.options('/api/push/subscribe', headers={
            'Origin': origin, 'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'content-type'})
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == origin
    assert response.headers['access-control-allow-credentials'] == 'true'
    assert 'POST' in response.headers['access-control-allow-methods']
    for name, value in HEADERS.items():
        assert response.headers[name] == value
