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
