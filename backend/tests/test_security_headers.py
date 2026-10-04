"""Read-only HTTP smoke coverage for response headers and unchanged CORS."""
import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException
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


async def test_handled_500_preserves_json_response_and_headers():
    app = create_app()
    @app.get('/test-handled-error')
    async def handled():
        raise HTTPException(500, 'Handled test failure')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url='http://test') as client:
        response = await client.get('/test-handled-error')
    assert response.status_code == 500
    assert response.json() == {'detail': 'Handled test failure'}
    assert response.headers['content-type'] == 'application/json'
    for name, value in HEADERS.items():
        assert response.headers[name] == value


@pytest.mark.parametrize('middleware_failure', [False, True])
async def test_unexpected_500_preserves_default_body_and_has_security_headers(middleware_failure):
    app = create_app()
    @app.get('/test-unhandled-error')
    async def unhandled():
        raise RuntimeError('Synthetic exception details must not reach the response')
    if middleware_failure:
        class FailingMiddleware:
            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                raise RuntimeError('Synthetic outer user-middleware failure')
        app.add_middleware(FailingMiddleware)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                base_url='http://test') as client:
        response = await client.get('/test-unhandled-error', headers={'Origin': settings.cors_origins[0]})
    assert response.status_code == 500
    assert response.text == 'Internal Server Error'
    assert response.headers['content-type'] == 'text/plain; charset=utf-8'
    assert response.headers['content-length'] == str(len('Internal Server Error'))
    for name, value in HEADERS.items():
        assert response.headers[name] == value
    assert 'content-security-policy' not in response.headers
    # The prior outer-generated 500 had no CORS headers; preserve that behavior.
    assert 'access-control-allow-origin' not in response.headers
    assert 'access-control-allow-credentials' not in response.headers


async def test_unhandled_exception_still_propagates_for_server_logging():
    app = create_app()
    @app.get('/test-unhandled-error')
    async def unhandled():
        raise RuntimeError('Synthetic exception for server logging')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url='http://test') as client:
        with pytest.raises(RuntimeError, match='Synthetic exception for server logging'):
            await client.get('/test-unhandled-error')


async def test_normal_cors_response_is_preserved():
    origin = settings.cors_origins[0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                base_url='http://test') as client:
        response = await client.get('/health', headers={'Origin': origin})
    assert response.status_code == 200 and response.json() == {'ok': True}
    assert response.headers['access-control-allow-origin'] == origin
    assert response.headers['access-control-allow-credentials'] == 'true'


def test_frontend_vercel_headers_and_rewrites_remain_configured():
    root = Path(__file__).resolve().parents[2]
    config = json.loads((root / 'frontend/vercel.json').read_text())
    assert config['headers'] == [{
        'source': '/(.*)', 'headers': [{'key': name, 'value': value} for name, value in HEADERS.items()],
    }]
    assert config['rewrites'] == [
        {'source': '/api/:path*', 'destination': 'https://sports-friday-backend.vercel.app/api/:path*'},
        {'source': '/((?!api/).*)', 'destination': '/index.html'},
    ]
