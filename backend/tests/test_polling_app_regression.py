"""Exercise existing authenticated app flows against the migrated local DB."""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx

from app import db
from app.config import settings
from app.main import create_app
from app.polling_schema import ensure_polling_schema
from app.sports_api.mock import build_timeline


async def test_auth_search_follow_preferences_match_and_push_subscription(monkeypatch):
    await db.connect()
    await ensure_polling_schema()
    run = uuid4().hex
    team_name = 'SmokeClub' + run
    home_ext, away_ext = 'home-' + run, 'away-' + run
    match_ext = 'match-' + run
    raw = build_timeline(match_ext)[0][1]
    fixture = raw['response'][0]
    fixture['fixture']['date'] = datetime.now(UTC).isoformat()
    fixture['teams']['home'].update(id=home_ext, name=team_name)
    fixture['teams']['away'].update(id=away_ext, name='Away ' + run)
    app = create_app()
    app.state.sports_client = SimpleNamespace(
        search_teams=AsyncMock(return_value={'response': [{'team': {'id': home_ext, 'name': team_name}}]}),
        get_team_fixtures=AsyncMock(return_value=raw))
    monkeypatch.setattr(settings, 'cron_secret', 'test-cron-auth')
    user_id = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.get('/api/auth/me')).status_code == 401
            credentials = {'email': 'smoke-' + run + '@example.com', 'password': 'local-test-password'}
            signup = await client.post('/api/auth/signup', json=credentials)
            assert signup.status_code == 201
            user_id = UUID(signup.json()['user']['id'])
            assert (await client.get('/api/auth/me')).status_code == 200
            teams = await client.get('/api/teams/search', params={'q': team_name})
            assert teams.status_code == 200
            team_id = teams.json()['teams'][0]['id']
            follow = await client.post('/api/subscriptions', json={'teamId': team_id})
            assert follow.status_code == 201 and follow.json()['subscription']['warning'] is None
            sub_id = follow.json()['subscription']['id']
            prefs = await client.patch('/api/subscriptions/' + sub_id, json={'notifyCards': False})
            assert prefs.status_code == 200 and prefs.json()['subscription']['notify_cards'] is False
            followed = (await client.get('/api/subscriptions')).json()['subscriptions']
            assert len(followed) == 1 and followed[0]['notify_cards'] is False
            matches = await client.get('/api/matches/live')
            assert matches.status_code == 200 and len(matches.json()['matches']) == 1
            saved = matches.json()['matches'][0]
            assert saved['external_id'] == match_ext
            assert (await client.get('/api/matches/' + saved['id'])).status_code == 200
            push = await client.post('/api/push/subscribe', json={
                'endpoint': 'https://push.example/' + run,
                'keys': {'p256dh': 'test-p256dh', 'auth': 'test-auth'}})
            assert push.status_code == 201
            assert (await client.get('/api/cron/poll')).status_code == 401
            assert (await client.get('/health')).status_code == 200
            await client.post('/api/auth/logout')
            assert (await client.get('/api/auth/me')).status_code == 401
            assert (await client.post('/api/auth/login', json=credentials)).status_code == 200
    finally:
        if user_id is not None:
            await db.execute('DELETE FROM users WHERE id=$1', user_id)
        await db.execute('DELETE FROM matches WHERE external_id=$1', match_ext)
        await db.execute('DELETE FROM teams WHERE external_id=ANY($1::text[])', [home_ext, away_ext])
        await db.disconnect()
