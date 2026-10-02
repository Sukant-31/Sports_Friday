from unittest.mock import AsyncMock

import pytest

from app.workers import cron_poll


async def test_cron_logs_polling_traceback_and_closes_client(monkeypatch, caplog):
    monkeypatch.setattr(cron_poll, 'ensure_notification_delivery_schema', AsyncMock())
    client = AsyncMock()
    monkeypatch.setattr(cron_poll, 'SportsApiClient', lambda: client)
    monkeypatch.setattr(cron_poll, 'discover', AsyncMock(return_value=1))
    monkeypatch.setattr(cron_poll, 'tick', AsyncMock(side_effect=RuntimeError('poll query failed')))
    with pytest.raises(RuntimeError, match='poll query failed'):
        await cron_poll.run_once()
    client.aclose.assert_awaited_once()
    assert any(r.exc_info and 'after fixture discovery' in r.message for r in caplog.records)


async def test_cron_runs_polling_after_successful_discovery(monkeypatch):
    monkeypatch.setattr(cron_poll, 'ensure_notification_delivery_schema', AsyncMock())
    client = AsyncMock()
    monkeypatch.setattr(cron_poll, 'SportsApiClient', lambda: client)
    monkeypatch.setattr(cron_poll, 'discover', AsyncMock(return_value=1))
    tick = AsyncMock()
    monkeypatch.setattr(cron_poll, 'tick', tick)
    result = await cron_poll.run_once()
    assert result['discovered'] == 1
    assert result['ok'] is True
    assert result['status'] == 'success'
    tick.assert_awaited_once()
    assert tick.await_args.args[0] is client
    assert 'report' in tick.await_args.kwargs
    client.aclose.assert_awaited_once()


async def test_cron_reports_failed_discovery_but_still_polls(monkeypatch):
    monkeypatch.setattr(cron_poll, 'ensure_notification_delivery_schema', AsyncMock())
    client = AsyncMock()
    monkeypatch.setattr(cron_poll, 'SportsApiClient', lambda: client)
    monkeypatch.setattr(cron_poll, 'discover', AsyncMock(side_effect=RuntimeError('private credentials')))
    tick = AsyncMock()
    monkeypatch.setattr(cron_poll, 'tick', tick)
    result = await cron_poll.run_once()
    assert result['ok'] is False
    assert result['status'] == 'partial_failure'
    assert result['operations']['discovery']['failed'] == 1
    assert result['failures'] == [{'stage': 'discovery', 'resource': 'pass', 'error_type': 'RuntimeError'}]
    assert 'private credentials' not in str(result)
    tick.assert_awaited_once()


async def test_cron_reports_notification_failure_even_when_retry_succeeds(monkeypatch):
    monkeypatch.setattr(cron_poll, 'ensure_notification_delivery_schema', AsyncMock())
    client = AsyncMock()
    monkeypatch.setattr(cron_poll, 'SportsApiClient', lambda: client)
    monkeypatch.setattr(cron_poll, 'discover', AsyncMock(return_value=1))
    delivery = AsyncMock(side_effect=[RuntimeError('push failed'), None])
    monkeypatch.setattr(cron_poll, 'deliver_event_notification', delivery)
    async def tick(client, notify, *, report):
        with pytest.raises(RuntimeError):
            await notify({'match_event_id': 'event'})
        await notify({'match_event_id': 'event'})
    monkeypatch.setattr(cron_poll, 'tick', tick)
    result = await cron_poll.run_once()
    assert result['ok'] is False
    assert result['operations']['notifications'] == {'succeeded': 1, 'failed': 1}


async def test_partial_failure_returns_503_with_summary(monkeypatch):
    import httpx
    from app import main
    from app.config import settings
    from app.workers.run_report import RunReport
    report = RunReport()
    report.record('polling', 'match', RuntimeError('database secret'))
    summary = report.response(1)
    monkeypatch.setattr(settings, 'cron_secret', 'test')
    monkeypatch.setattr(main, 'run_once', AsyncMock(return_value=summary))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.create_app()),
                                base_url='http://test') as client:
        result = await client.get('/api/cron/poll', headers={'Authorization': 'Bearer test'})
    assert result.status_code == 503
    assert result.json() == summary


async def test_tick_counts_failed_match_and_continues(monkeypatch):
    from app.workers import poller
    from app.workers.run_report import RunReport
    monkeypatch.setattr(poller.matches_repo, 'find_pollable_matches', AsyncMock(
        return_value=[{'id': 'failed'}, {'id': 'good'}]))
    poll = AsyncMock(side_effect=[RuntimeError('failed'), None])
    monkeypatch.setattr(poller, 'poll_match', poll)
    monkeypatch.setattr(poller.events_repo, 'pending_events', AsyncMock(return_value=[]))
    report = RunReport()
    await poller.tick(object(), AsyncMock(), report=report)
    assert report.operations['polling'] == {'succeeded': 1, 'failed': 1}
    assert poll.await_count == 2


async def test_discovery_counts_partial_upserts_and_failed_teams(monkeypatch):
    from app.sports_api.client import SportsApiError
    from app.workers import discovery
    from app.workers.run_report import RunReport
    monkeypatch.setattr(discovery, 'repair_team_names', AsyncMock())
    monkeypatch.setattr(discovery.teams_repo, 'find_subscribed_teams', AsyncMock(
        return_value=[{'external_id': 'bad'}, {'external_id': 'good'}]))
    async def discover_team(client, external_id, *, refresh, report):
        report.fixtures_upserted += 1
        if external_id == 'bad':
            raise SportsApiError('quota')
        return 1
    monkeypatch.setattr(discovery, 'discover_team', discover_team)
    report = RunReport()
    assert await discovery.discover(object(), report=report) == 1
    assert report.operations['discovery'] == {'succeeded': 1, 'failed': 1}
    assert report.fixtures_upserted == 2
