from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.team_search import match_rank, rank_teams
from app.services import team_service


NAMES = ['Manchester United', 'Manchester City', 'FC United of Manchester',
         'Leeds United', 'Newcastle United', 'West Ham United', 'Sheffield United']


def teams(names=NAMES):
    return [dict(id=str(i), external_id=str(i), name=name) for i, name in enumerate(names)]


@pytest.mark.parametrize('query,expected', [
    ('Manchester United', ['Manchester United']),
    ('mAnChEsTeR', ['Manchester City', 'Manchester United', 'FC United of Manchester']),
    ('man uni', ['Manchester United']),
    ('Manchster', ['Manchester City', 'Manchester United', 'FC United of Manchester']),
    ('chester', ['Manchester City', 'Manchester United', 'FC United of Manchester']),
    ('zzzzzz', []), ('', []), ('   ', []),
])
def test_matching(query, expected):
    assert [t['name'] for t in rank_teams(teams(), query)] == expected


def test_united_returns_all_word_matches():
    assert {t['name'] for t in rank_teams(teams(), 'United')} == set(NAMES) - {'Manchester City'}


def test_priority():
    names = ['Leeds United', 'United FC', 'United', 'Disunited', 'Untied']
    assert [t['name'] for t in rank_teams(teams(names), 'United')] == [
        'United', 'United FC', 'Leeds United', 'Disunited', 'Untied',
    ]


def test_duplicates_and_limit():
    data = teams([f'United {i}' for i in range(30)])
    duplicate = dict(data[0], id='other')
    result = rank_teams([*data, duplicate], 'United')
    assert len(result) == 15
    assert len({t['external_id'] for t in result}) == 15


def test_short_queries_do_not_fuzzy_match():
    assert match_rank('Arsenal', 'ax') is None


async def test_empty_query_never_calls_database_or_provider(monkeypatch):
    cached = AsyncMock()
    monkeypatch.setattr(team_service.teams_repo, 'search_teams_cached', cached)
    assert await team_service.search_teams(None, ' ') == {'teams': [], 'warning': None}
    cached.assert_not_awaited()


async def test_provider_merge_is_ranked_and_deduplicated(monkeypatch):
    saved = teams()
    monkeypatch.setattr(team_service.teams_repo, 'search_teams_cached', AsyncMock(return_value=saved))
    monkeypatch.setattr(team_service.teams_repo, 'upsert_team', AsyncMock(return_value=saved[0]))
    client = SimpleNamespace(search_teams=AsyncMock(return_value={'response': [
        {'team': {'id': 0, 'name': 'Manchester United'}},
    ]}))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(sports_client=client)))
    result = await team_service.search_teams(request, 'man uni')
    assert [t['name'] for t in result['teams']] == ['Manchester United']
    client.search_teams.assert_awaited_once_with('man')


async def test_repository_candidates_on_postgres(monkeypatch):
    """Use a temporary catalogue to test the real SQL in isolation."""
    import asyncpg
    from app.config import settings
    from app.repositories import teams as repo

    connection = await asyncpg.connect(settings.database_url, timeout=3)
    try:
        await connection.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        await connection.execute('''CREATE TEMP TABLE teams (
            id text, external_id text, name text, league text)''')
        await connection.executemany(
            'INSERT INTO teams (id, external_id, name) VALUES ($1, $2, $3)',
            [(t['id'], t['external_id'], t['name']) for t in teams()],
        )
        await connection.execute('CREATE INDEX ON teams USING gin (lower(name) gin_trgm_ops)')
        monkeypatch.setattr(repo.db, 'fetch', connection.fetch)
        for query, expected in [
            ('Manchester', {'Manchester City', 'Manchester United', 'FC United of Manchester'}),
            ('UNITED', set(NAMES) - {'Manchester City'}),
            ('man uni', {'Manchester United'}),
            ('Manchster', {'Manchester City', 'Manchester United', 'FC United of Manchester'}),
            ('zzzzzz', set()), ('', set()),
        ]:
            found = await repo.search_teams_cached(query)
            assert {t['name'] for t in rank_teams(found, query)} == expected
    finally:
        await connection.close()


async def test_rich_cache_avoids_provider(monkeypatch):
    data = teams([f'United {i}' for i in range(30)])
    monkeypatch.setattr(team_service.teams_repo, 'search_teams_cached', AsyncMock(return_value=data))
    client = SimpleNamespace(search_teams=AsyncMock())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(sports_client=client)))
    result = await team_service.search_teams(request, 'United')
    assert len(result['teams']) == 15
    client.search_teams.assert_not_awaited()


def test_multiword_typo_requires_each_word():
    assert [t['name'] for t in rank_teams(teams(), 'manchster unuted')] == ['Manchester United']
    assert rank_teams(teams(), 'Manchester banana') == []


async def test_missing_trigram_extension_falls_back_to_standard_sql(monkeypatch):
    import asyncpg
    from app.repositories import teams as repo

    fetch = AsyncMock(side_effect=[asyncpg.UndefinedFunctionError('operator does not exist'), teams()])
    monkeypatch.setattr(repo.db, 'fetch', fetch)
    found = await repo.search_teams_cached('Manchster')
    assert [t['name'] for t in rank_teams(found, 'Manchster')] == [
        'Manchester City', 'Manchester United', 'FC United of Manchester',
    ]
    assert fetch.await_count == 2
    assert 'word_similarity' not in fetch.await_args.args[0]


async def test_other_database_errors_are_not_hidden(monkeypatch):
    import asyncpg
    from app.repositories import teams as repo

    fetch = AsyncMock(side_effect=asyncpg.UndefinedTableError('teams missing'))
    monkeypatch.setattr(repo.db, 'fetch', fetch)
    with pytest.raises(asyncpg.UndefinedTableError):
        await repo.search_teams_cached('Manchester')
    assert fetch.await_count == 1


async def test_authenticated_search_without_trigram_extension(monkeypatch):
    """Reproduce production's missing operators through the real HTTP route."""
    import uuid
    import asyncpg
    import httpx
    from app.config import settings
    from app.deps import get_current_user_id
    from app.main import create_app
    from app.repositories import teams as repo

    connection = await asyncpg.connect(settings.database_url, timeout=3)
    try:
        await connection.execute('''CREATE TEMP TABLE teams (
            id uuid, external_id text, name text, league text)''')
        await connection.executemany(
            'INSERT INTO teams (id, external_id, name) VALUES ($1,$2,$3)',
            [(uuid.uuid4(), t['external_id'], t['name']) for t in teams()],
        )
        # Hide extensions in public without changing or dropping them.
        await connection.execute('SET search_path TO pg_temp')
        fetch = AsyncMock(wraps=connection.fetch)
        monkeypatch.setattr(repo.db, 'fetch', fetch)
        app = create_app()
        app.dependency_overrides[get_current_user_id] = lambda: uuid.uuid4()
        app.state.sports_client = SimpleNamespace(search_teams=AsyncMock(
            return_value={'response': []},
        ))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            for query in ('Manchester', 'man uni', 'Manchster'):
                response = await client.get('/api/teams/search', params={'q': query})
                assert response.status_code == 200
                assert any(t['name'] == 'Manchester United' for t in response.json()['teams'])
            response = await client.get('/api/teams/search', params={'q': 'zzzzzz'})
            assert response.status_code == 200
            assert response.json()['teams'] == []
        assert fetch.await_count == 8  # Missing extension, then successful fallback each time.
    finally:
        await connection.close()
