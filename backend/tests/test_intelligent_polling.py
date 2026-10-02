from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.sports_api.client import ApiRequestSkipped
from app.sports_api.mock import build_timeline
from app.workers import discovery, poller
from app.workers.polling_policy import decide
from app.workers.run_report import RunReport

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


def match(status='scheduled', offset=0, **extra):
    return {'id': 'stored', 'external_id': 'fixture', 'status': status,
            'starts_at': NOW + timedelta(minutes=offset), 'home_team_id': 'home',
            'away_team_id': 'away', 'home_score': 0, 'away_score': 0, **extra}


@pytest.fixture(autouse=True)
def timing(monkeypatch):
    monkeypatch.setattr(settings, 'live_poll_interval_seconds', 180)
    monkeypatch.setattr(settings, 'pre_match_window_minutes', 15)
    monkeypatch.setattr(settings, 'pre_match_poll_interval_seconds', 900)
    monkeypatch.setattr(settings, 'expected_match_minutes', 120)
    monkeypatch.setattr(settings, 'post_match_grace_minutes', 30)
    monkeypatch.setattr(settings, 'status_recovery_hours', 6)


@pytest.mark.parametrize('row', [match(offset=60), match(status='finished', offset=-120),
                                 match(offset=-400), match(status='live', offset=-400)])
def test_distant_finished_and_expired_recovery_need_no_poll(row):
    assert decide(row, NOW) is None


def test_kickoff_window_and_live_priorities():
    assert decide(match(offset=15), NOW).reason == 'near_kickoff'
    assert decide(match(offset=16), NOW) is None
    assert decide(match(status='live', offset=-40), NOW).priority == 'high'
    assert decide(match(status='live', starts_at=None), NOW).priority == 'high'
    assert decide(match(offset=-125), NOW).priority == 'medium'
    assert decide(match(status='live', offset=-160), NOW).reason == 'unresolved_final_status'


def test_checked_timestamp_and_live_transition_cadence():
    assert decide(match(status='live', last_checked_at=NOW-timedelta(seconds=179)), NOW) is None
    assert decide(match(status='live', last_checked_at=NOW-timedelta(seconds=180)), NOW)
    # A pre-kickoff check doesn't defer active polling for its full 15-minute interval.
    assert decide(match(offset=-1, last_checked_at=NOW-timedelta(minutes=4)), NOW)


async def setup_tick(monkeypatch, rows):
    monkeypatch.setattr(poller.matches_repo, 'find_pollable_matches', AsyncMock(return_value=rows))
    monkeypatch.setattr(poller.matches_repo, 'mark_checked', AsyncMock())
    monkeypatch.setattr(poller.events_repo, 'pending_events', AsyncMock(return_value=[]))
    process = AsyncMock()
    monkeypatch.setattr(poller, 'process_fixture', process)
    return process


async def test_future_or_finished_match_and_empty_tick_make_no_api_calls(monkeypatch):
    now = datetime.now(timezone.utc)
    client = SimpleNamespace(get_live_fixtures=AsyncMock(), get_date_fixtures=AsyncMock())
    for rows in ([], [match(starts_at=now+timedelta(days=1))],
                 [match(status='finished', starts_at=now)]):
        await setup_tick(monkeypatch, rows)
        report = RunReport()
        await poller.tick(client, AsyncMock(), report=report)
        assert report.fixtures_requiring_polling == 0
    client.get_live_fixtures.assert_not_awaited()
    client.get_date_fixtures.assert_not_awaited()


async def test_many_relevant_matches_share_live_feed_and_ignore_other_matches(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [match(status='live', starts_at=now-timedelta(minutes=30),
                  id=str(i), external_id=str(i)) for i in range(8)]
    process = await setup_tick(monkeypatch, rows)
    raw = {'response': [build_timeline(str(i))[0][1]['response'][0] for i in range(9)]}
    client = SimpleNamespace(get_live_fixtures=AsyncMock(return_value=raw),
                             get_date_fixtures=AsyncMock())
    report = RunReport()
    await poller.tick(client, AsyncMock(), report=report)
    client.get_live_fixtures.assert_awaited_once()
    client.get_date_fixtures.assert_not_awaited()
    assert process.await_count == 8
    assert report.fixture_priorities == {'high': 8, 'medium': 0, 'low': 0}


async def test_near_kickoff_without_live_match_only_uses_shared_date_feed(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [match(starts_at=now+timedelta(minutes=10), id=str(i), external_id=str(i)) for i in range(8)]
    await setup_tick(monkeypatch, rows)
    client = SimpleNamespace(get_live_fixtures=AsyncMock(),
                             get_date_fixtures=AsyncMock(return_value={'response': []}))
    await poller.tick(client, AsyncMock())
    client.get_live_fixtures.assert_not_awaited()
    client.get_date_fixtures.assert_awaited_once()
    assert poller.matches_repo.mark_checked.await_count == 8


async def test_final_whistle_uses_date_feed_after_fixture_leaves_live_feed(monkeypatch):
    now = datetime.now(timezone.utc)
    process = await setup_tick(monkeypatch, [match(status='live', starts_at=now-timedelta(minutes=120))])
    client = SimpleNamespace(get_live_fixtures=AsyncMock(return_value={'response': []}),
        get_date_fixtures=AsyncMock(return_value=build_timeline('fixture')[-1][1]))
    await poller.tick(client, AsyncMock())
    assert process.await_args.args[2]['status'] == 'finished'


async def test_budget_skip_still_retries_pending_notifications(monkeypatch):
    now = datetime.now(timezone.utc)
    await setup_tick(monkeypatch, [match(status='live', starts_at=now)])
    event = {'id': 'event', 'match_id': 'stored', 'team_id': 'home', 'type': 'goal', 'detail': {}}
    monkeypatch.setattr(poller.events_repo, 'pending_events', AsyncMock(return_value=[event]))
    client = SimpleNamespace(get_live_fixtures=AsyncMock(side_effect=ApiRequestSkipped('budget_exhausted')),
                             get_date_fixtures=AsyncMock())
    notify = AsyncMock()
    report = RunReport()
    await poller.tick(client, notify, report=report)
    assert not report.failures
    client.get_date_fixtures.assert_not_awaited()
    notify.assert_awaited_once()


async def test_processing_failure_does_not_block_other_matches(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [match(status='live', starts_at=now, id=str(i), external_id=str(i)) for i in range(2)]
    process = await setup_tick(monkeypatch, rows)
    process.side_effect = [RuntimeError('failed'), None]
    client = SimpleNamespace(get_live_fixtures=AsyncMock(return_value={'response': [
        build_timeline(str(i))[0][1]['response'][0] for i in range(2)]}))
    report = RunReport()
    await poller.tick(client, AsyncMock(), report=report)
    assert report.operations['polling'] == {'succeeded': 1, 'failed': 1}


async def test_discovery_refresh_is_persisted_and_shared_fixture_upserted_once(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(discovery, 'repair_team_names', AsyncMock())
    teams = [{'external_id': str(i), 'last_discovered_at': None} for i in (100, 200)]
    monkeypatch.setattr(discovery.teams_repo, 'find_subscribed_teams', AsyncMock(return_value=teams))
    monkeypatch.setattr(discovery.teams_repo, 'upsert_team', AsyncMock(return_value={'id': 'team'}))
    monkeypatch.setattr(discovery.teams_repo, 'mark_discovered', AsyncMock())
    monkeypatch.setattr(discovery.matches_repo, 'upsert_match', AsyncMock())
    client = SimpleNamespace(get_team_fixtures=AsyncMock(return_value=build_timeline('fixture')[0][1]),
                             get_team_live_fixtures=AsyncMock())
    report = RunReport()
    assert await discovery.discover(client, report=report) == 1
    assert report.followed_teams == 2
    discovery.matches_repo.upsert_match.assert_awaited_once()
    assert discovery.teams_repo.mark_discovered.await_count == 2
    client.get_team_live_fixtures.assert_not_awaited()
    client.get_team_fixtures.reset_mock()
    for team in teams:
        team['last_discovered_at'] = now
    assert await discovery.discover(client) == 0
    client.get_team_fixtures.assert_not_awaited()


async def test_cron_nothing_due_is_success_with_zero_external_requests(monkeypatch, cron_runtime):
    from app.workers import cron_poll
    client = cron_runtime()
    monkeypatch.setattr(cron_poll, 'SportsApiClient', lambda: client)
    monkeypatch.setattr(cron_poll, 'ensure_notification_delivery_schema', AsyncMock())
    monkeypatch.setattr(cron_poll, 'discover', AsyncMock(return_value=0))
    monkeypatch.setattr(cron_poll, 'tick', AsyncMock())
    result = await cron_poll.run_once()
    assert result['ok'] and result['status'] == 'nothing_due'
    assert result['api_requests']['made'] == 0
    assert result['api_budget']['remaining'] == 90


def test_safe_reports_distinguish_budget_api_db_and_notification_failures():
    from app.sports_api.client import SportsApiError
    report = RunReport(api_requests={'skip_reasons': {'budget_exhausted': 1}})
    assert report.response(0)['status'] == 'budget_exhausted'
    report.record('polling', 'live_feed', SportsApiError('private provider information'))
    report.record('notifications', 'event', RuntimeError('private push information'))
    assert report.response(0)['status'] == 'partial_failure'
    assert {f['category'] for f in report.failures} == {'api_football_failure', 'notification_failure'}
    report.record('database', 'state', RuntimeError('private database information'))
    assert report.response(0)['status'] == 'database_failure'
    assert 'private' not in str(report.response(0))


async def test_overlapping_cron_is_a_reported_successful_skip(monkeypatch, cron_runtime):
    from contextlib import asynccontextmanager
    from app.workers import cron_poll
    @asynccontextmanager
    async def locked(*args):
        yield False
    client = cron_runtime()
    monkeypatch.setattr(cron_poll, 'SportsApiClient', lambda: client)
    monkeypatch.setattr(cron_poll, 'ensure_notification_delivery_schema', AsyncMock())
    monkeypatch.setattr(cron_poll.sports_requests, 'lock', locked)
    poll = AsyncMock()
    monkeypatch.setattr(cron_poll, 'tick', poll)
    result = await cron_poll.run_once()
    assert result['ok'] and result['status'] == 'skipped'
    assert result['skip_reason'] == 'overlapping_run'
    poll.assert_not_awaited()
