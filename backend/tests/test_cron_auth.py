"""Cron authorization gates all request-triggered work; polling is always mocked."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app import main
from app.config import settings

SECRET = 'synthetic-cron-test-value'
SUMMARY = {'ok': True, 'status': 'nothing_due', 'discovered': 0}


@pytest.fixture
async def cron_client(monkeypatch):
    monkeypatch.setattr(settings, 'cron_secret', SECRET)
    monkeypatch.setattr(settings, 'env', 'production')
    monkeypatch.delenv('VERCEL_ENV', raising=False)
    polling = AsyncMock(return_value=SUMMARY)
    monkeypatch.setattr(main, 'run_once', polling)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.create_app()),
                                base_url='https://test') as client:
        yield client, polling


@pytest.mark.parametrize('header', [None, '', 'Bearer', 'Bearer ', 'Bearer wrong',
                                   'Basic synthetic-cron-test-value', 'bearer ' + SECRET,
                                   'Bearer  ' + SECRET, 'Bearer ' + SECRET + ' ',
                                   SECRET, 'Bearer non-ascii-é'])
async def test_invalid_authorization_rejected_before_polling(cron_client, header, caplog):
    client, polling = cron_client
    headers = {} if header is None else {'Authorization': header}
    if header == 'Bearer non-ascii-é':
        headers = {b'Authorization': header.encode('utf-8')}
    response = await client.get('/api/cron/poll', headers=headers)
    assert response.status_code == 401
    assert response.json() == {'detail': 'unauthorized'}
    assert SECRET not in response.text and SECRET not in caplog.text
    polling.assert_not_awaited()


async def test_valid_cron_bearer_preserves_response(cron_client):
    client, polling = cron_client
    response = await client.get('/api/cron/poll', headers={'Authorization': f'Bearer {SECRET}'})
    assert response.status_code == 200
    assert response.json() == SUMMARY
    polling.assert_awaited_once_with()


async def test_alternative_credentials_do_not_authorize(cron_client):
    client, polling = cron_client
    client.cookies.set('cron_secret', SECRET)
    response = await client.get('/api/cron/poll', params={'token': SECRET, 'CRON_SECRET': SECRET},
                                headers={'X-Cron-Secret': SECRET})
    assert response.status_code == 401
    polling.assert_not_awaited()


async def test_duplicate_authorization_headers_rejected(cron_client):
    client, polling = cron_client
    response = await client.get('/api/cron/poll', headers=[
        ('Authorization', f'Bearer {SECRET}'), ('Authorization', 'Bearer wrong')])
    assert response.status_code == 401
    polling.assert_not_awaited()


@pytest.mark.parametrize('env,vercel', [('production', None), ('development', 'production')])
@pytest.mark.parametrize('secret', ['', '   '])
async def test_production_missing_secret_fails_closed(cron_client, monkeypatch, env, vercel, secret):
    client, polling = cron_client
    monkeypatch.setattr(settings, 'env', env)
    monkeypatch.setattr(settings, 'cron_secret', secret)
    if vercel:
        monkeypatch.setenv('VERCEL_ENV', vercel)
    response = await client.get('/api/cron/poll', headers={'Authorization': 'Bearer '})
    assert response.status_code == 503
    assert response.json() == {'detail': 'Cron authentication unavailable'}
    polling.assert_not_awaited()


async def test_local_no_secret_mode_is_preserved(cron_client, monkeypatch):
    client, polling = cron_client
    monkeypatch.setattr(settings, 'env', 'development')
    monkeypatch.setattr(settings, 'cron_secret', '')
    response = await client.get('/api/cron/poll')
    assert response.status_code == 200
    polling.assert_awaited_once_with()


async def test_valid_auth_preserves_partial_failure(cron_client):
    client, polling = cron_client
    summary = {'ok': False, 'status': 'partial_failure'}
    polling.return_value = summary
    response = await client.get('/api/cron/poll', headers={'Authorization': f'Bearer {SECRET}'})
    assert response.status_code == 503
    assert response.json() == summary


async def test_trailing_slash_and_alternate_method_cannot_bypass_auth(cron_client):
    client, polling = cron_client
    assert (await client.get('/api/cron/poll/', follow_redirects=True)).status_code == 401
    assert (await client.post('/api/cron/poll')).status_code == 405
    polling.assert_not_awaited()
