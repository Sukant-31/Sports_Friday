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
    assert await cron_poll.run_once() == {'discovered': 1}
    tick.assert_awaited_once_with(client, cron_poll.deliver_event_notification)
    client.aclose.assert_awaited_once()
