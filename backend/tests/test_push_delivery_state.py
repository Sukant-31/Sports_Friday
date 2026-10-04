"""Delivery ledger integration and real transport outcomes; all HTTP is mocked."""
import asyncio
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pywebpush import WebPushException
from requests.exceptions import Timeout

from app import db
from app.notification_schema import _ensure_schema
from app.repositories import push_subscriptions as push_repo
from app.workers import notifier, web_push
from app.workers.run_report import RunReport
from tests import test_notification_schema, test_pipeline, test_push_destination

infra = test_pipeline.infra
fixtures = test_pipeline.fixtures
transport = test_push_destination.transport


@pytest.mark.parametrize('status', [301, 403])
async def test_permanent_failure_with_inherited_read_only_status(transport, monkeypatch, status):
    # Reproduce newer pywebpush's property even with an older local dependency.
    monkeypatch.setattr(WebPushException, 'status_code',
                        property(lambda self: getattr(self.response, 'status_code', None)),
                        raising=False)
    target, response, network, delete = transport
    response.status_code = status
    with pytest.raises(web_push.PermanentPushError) as error:
        await web_push.send_push(target, {'title': 'Test'})
    assert error.value.status_code == status
    assert error.value.response is None
    network.assert_called_once()
    delete.assert_not_awaited()


@pytest.mark.parametrize('status', [200, 201, 202])
async def test_provider_acceptance_is_explicit(transport, status):
    target, response, network, delete = transport
    response.status_code = status
    assert await web_push.send_push(target, {'title': 'Test'}) == 'accepted'
    network.assert_called_once()
    delete.assert_not_awaited()


@pytest.mark.parametrize('status', [404, 410])
async def test_expiry_is_not_acceptance(transport, status):
    target, response, network, delete = transport
    response.status_code = status
    assert await web_push.send_push(target, {'title': 'Test'}) == 'expired'
    network.assert_called_once()
    delete.assert_awaited_once_with(target['push_id'])


@pytest.mark.parametrize('status', [400, 401, 403, 413, 301])
async def test_permanent_provider_failure_is_explicit(transport, status):
    target, response, network, delete = transport
    response.status_code = status
    with pytest.raises(web_push.PermanentPushError) as error:
        await web_push.send_push(target, {'title': 'Test'})
    assert error.value.status_code == status
    network.assert_called_once()
    delete.assert_not_awaited()


@pytest.mark.parametrize('status', [408, 425, 429, 500, 502, 503])
async def test_transient_failure_remains_retryable(transport, status):
    target, response, network, delete = transport
    response.status_code = status
    with pytest.raises(WebPushException) as error:
        await web_push.send_push(target, {'title': 'Test'})
    assert not isinstance(error.value, web_push.PermanentPushError)
    network.assert_called_once()
    delete.assert_not_awaited()


async def test_console_is_simulation_not_acceptance(transport, monkeypatch):
    target, _response, network, _delete = transport
    monkeypatch.setattr(web_push.settings, 'push_transport', 'console')
    assert await web_push.send_push(target, {'title': 'Test'}) == 'simulated'
    network.assert_not_called()


async def job_for(match, kind='goal'):
    return (await test_pipeline.lifecycle_jobs(match, kind))[0]


@pytest.mark.parametrize('kind', ['goal', 'card', 'kickoff', 'half_time', 'full_time'])
async def test_concurrent_attempts_have_one_acceptance_receipt(fixtures, monkeypatch, kind):
    job = await job_for(fixtures['match'], kind)
    send = AsyncMock(return_value='accepted')
    monkeypatch.setattr(notifier, 'send_push', send)
    await asyncio.gather(*(notifier.deliver_event_notification(job) for _ in range(4)))
    send.assert_awaited_once()
    event_id = UUID(job['match_event_id'])
    assert await db.pool().fetchval('SELECT count(*) FROM notification_deliveries WHERE event_id=$1', event_id) == 1
    assert await db.pool().fetchval('SELECT delivered_at IS NOT NULL FROM match_events WHERE id=$1', event_id)


async def test_permanent_failure_is_recorded_not_retried(fixtures, monkeypatch):
    job = await job_for(fixtures['match'])
    send = AsyncMock(side_effect=web_push.PermanentPushError(403))
    monkeypatch.setattr(notifier, 'send_push', send)
    with pytest.raises(web_push.PermanentPushError):
        await notifier.deliver_event_notification(job)
    await notifier.deliver_event_notification(job)
    send.assert_awaited_once()
    event_id = UUID(job['match_event_id'])
    row = await db.fetchrow('SELECT * FROM notification_terminal_outcomes WHERE event_id=$1', event_id)
    assert row['outcome'] == 'permanent_failure' and row['status_code'] == 403
    assert await db.pool().fetchval('SELECT count(*) FROM notification_deliveries WHERE event_id=$1', event_id) == 0
    assert await db.pool().fetchval('SELECT delivered_at IS NOT NULL FROM match_events WHERE id=$1', event_id)


async def test_multiple_devices_retry_only_transient_failure(fixtures, monkeypatch):
    user, match = fixtures['user'], fixtures['match']
    endpoints = [f'https://push.example/{label}-{uuid4()}' for label in ('permanent', 'transient')]
    for endpoint in endpoints:
        await push_repo.upsert_push_subscription(user['id'], endpoint, 'key', 'auth')
    job = await job_for(match)
    calls = []
    broken = True

    async def send(target, _payload):
        calls.append(target['endpoint'])
        if target['endpoint'] == endpoints[0]:
            raise web_push.PermanentPushError(400)
        if target['endpoint'] == endpoints[1] and broken:
            raise RuntimeError('Synthetic transient failure')
        return 'accepted'

    monkeypatch.setattr(notifier, 'send_push', send)
    with pytest.raises(RuntimeError):
        await notifier.deliver_event_notification(job)
    event_id = UUID(job['match_event_id'])
    assert await db.pool().fetchval('SELECT delivered_at FROM match_events WHERE id=$1', event_id) is None
    assert await db.pool().fetchval('SELECT count(*) FROM notification_deliveries WHERE event_id=$1', event_id) == 1
    broken = False
    await notifier.deliver_event_notification(job)
    assert len(calls) == 4
    assert calls.count(endpoints[0]) == 1
    assert calls.count(endpoints[1]) == 2
    assert await db.pool().fetchval('SELECT count(*) FROM notification_deliveries WHERE event_id=$1', event_id) == 2


@pytest.mark.parametrize('status', [404, 410])
async def test_expired_device_cleanup_creates_no_acceptance_receipt(fixtures, monkeypatch, status):
    job = await job_for(fixtures['match'])
    async def expired(target, _payload):
        await push_repo.delete_push_subscription_by_id(target['push_id'])
        return 'expired'
    send = AsyncMock(side_effect=expired)
    monkeypatch.setattr(notifier, 'send_push', send)
    await notifier.deliver_event_notification(job)
    await notifier.deliver_event_notification(job)
    send.assert_awaited_once()
    assert await db.pool().fetchval('SELECT count(*) FROM notification_deliveries WHERE event_id=$1', UUID(job['match_event_id'])) == 0


async def test_simulation_is_terminal_but_not_accepted(fixtures, monkeypatch):
    job = await job_for(fixtures['match'])
    send = AsyncMock(return_value='simulated')
    monkeypatch.setattr(notifier, 'send_push', send)
    await notifier.deliver_event_notification(job)
    await notifier.deliver_event_notification(job)
    send.assert_awaited_once()
    event_id = UUID(job['match_event_id'])
    assert await db.pool().fetchval('SELECT count(*) FROM notification_deliveries WHERE event_id=$1', event_id) == 0
    assert await db.pool().fetchval('SELECT outcome FROM notification_terminal_outcomes WHERE event_id=$1', event_id) == 'simulated'


async def test_lifecycle_other_team_does_not_retry_terminal_failure(fixtures, monkeypatch):
    match, _sent = await test_pipeline.capture_lifecycle(fixtures, monkeypatch)
    jobs = await test_pipeline.lifecycle_jobs(match, 'kickoff')
    send = AsyncMock(side_effect=web_push.PermanentPushError(400))
    monkeypatch.setattr(notifier, 'send_push', send)
    with pytest.raises(web_push.PermanentPushError):
        await notifier.deliver_event_notification(jobs[0])
    await notifier.deliver_event_notification(jobs[1])
    send.assert_awaited_once()


async def test_outcome_schema_upgrade_is_idempotent_and_preserves_receipts():
    conn = await test_notification_schema.legacy_connection()
    try:
        await _ensure_schema(conn)
        event = await conn.fetchval('SELECT id FROM match_events LIMIT 1')
        push = uuid4()
        await conn.execute('INSERT INTO push_subscriptions (id) VALUES ($1)', push)
        await conn.execute('INSERT INTO notification_deliveries VALUES ($1,$2)', event, push)
        await conn.execute('DROP TABLE notification_terminal_outcomes')
        await conn.execute('DROP TABLE schema_migrations')
        await _ensure_schema(conn)
        await _ensure_schema(conn)
        assert await conn.fetchval('SELECT count(*) FROM notification_deliveries') == 1
        assert await conn.fetchval("SELECT count(*) FROM schema_migrations WHERE name='009_push_delivery_outcomes.sql'") == 1
    finally:
        await conn.close()


def test_cron_summary_reports_acceptance_without_claiming_display():
    report = RunReport(notifications_generated=2, notifications_delivered=1)
    summary = report.response(0)['notifications']
    assert summary == {'generated': 2, 'accepted': 1, 'display_confirmation': 'unavailable', 'failed_operations': 0}
    assert 'delivered' not in summary


async def test_network_timeout_is_retryable_without_pruning(transport):
    target, _response, network, delete = transport
    network.side_effect = Timeout('Synthetic timeout')
    with pytest.raises(Timeout):
        await web_push.send_push(target, {'title': 'Test'})
    network.assert_called_once()
    delete.assert_not_awaited()


async def test_unvalidated_legacy_input_is_terminal_without_retry(fixtures, monkeypatch):
    job = await job_for(fixtures['match'])
    send = AsyncMock(side_effect=web_push.InvalidPushSubscription('Invalid transport input'))
    monkeypatch.setattr(notifier, 'send_push', send)
    with pytest.raises(ValueError):
        await notifier.deliver_event_notification(job)
    await notifier.deliver_event_notification(job)
    send.assert_awaited_once()
    assert await db.pool().fetchval(
        'SELECT outcome FROM notification_terminal_outcomes WHERE event_id=$1',
        UUID(job['match_event_id']),
    ) == 'permanent_failure'


async def test_server_configuration_errors_are_not_terminal(fixtures, monkeypatch):
    job = await job_for(fixtures['match'])
    send = AsyncMock(side_effect=ValueError('Synthetic server configuration error'))
    monkeypatch.setattr(notifier, 'send_push', send)
    with pytest.raises(ValueError):
        await notifier.deliver_event_notification(job)
    event_id = UUID(job['match_event_id'])
    assert await db.pool().fetchval('SELECT delivered_at FROM match_events WHERE id=$1', event_id) is None
    assert await db.pool().fetchval('SELECT count(*) FROM notification_terminal_outcomes WHERE event_id=$1', event_id) == 0


async def test_invalid_subscription_key_is_terminal_without_network(transport):
    target, _response, network, delete = transport
    target['p256dh'] = 'YQ=='
    with pytest.raises(web_push.InvalidPushSubscription):
        await web_push.send_push(target, {'title': 'Test'})
    network.assert_not_called()
    delete.assert_not_awaited()


@pytest.mark.parametrize('kind', ['goal', 'card', 'kickoff'])
@pytest.mark.parametrize('first_outcome', ['accepted', 'permanent_failure', 'simulated'])
async def test_crash_on_later_device_preserves_committed_outcome(
    fixtures, monkeypatch, kind, first_outcome,
):
    await push_repo.upsert_push_subscription(
        fixtures['user']['id'], f'https://push.example/crash-{uuid4()}', 'key', 'auth',
    )
    job = await job_for(fixtures['match'], kind)
    calls = []
    first_id = None

    async def crash_on_second(target, _payload):
        nonlocal first_id
        calls.append(target['push_id'])
        if len(calls) == 1:
            first_id = target['push_id']
            if first_outcome == 'permanent_failure':
                raise web_push.PermanentPushError(403)
            return first_outcome
        raise asyncio.CancelledError('Synthetic process interruption')

    monkeypatch.setattr(notifier, 'send_push', crash_on_second)
    with pytest.raises(asyncio.CancelledError):
        await notifier.deliver_event_notification(job)
    event_id = UUID(job['match_event_id'])
    table = 'notification_deliveries' if first_outcome == 'accepted' else 'notification_terminal_outcomes'
    assert await db.pool().fetchval(
        f'SELECT count(*) FROM {table} WHERE event_id=$1 AND push_id=$2', event_id, first_id,
    ) == 1
    assert await db.pool().fetchval('SELECT delivered_at FROM match_events WHERE id=$1', event_id) is None
    retry = AsyncMock(return_value='accepted')
    monkeypatch.setattr(notifier, 'send_push', retry)
    await notifier.deliver_event_notification(job)
    retry.assert_awaited_once()
    assert retry.await_args.args[0]['push_id'] != first_id
    await notifier.deliver_event_notification(job)
    retry.assert_awaited_once()


@pytest.mark.parametrize('kind', ['goal', 'card', 'kickoff'])
async def test_acceptance_before_receipt_crash_has_explicit_uncertainty(fixtures, monkeypatch, kind):
    job = await job_for(fixtures['match'], kind)
    provider_acceptances = []

    async def accepted_but_interrupted(target, _payload):
        provider_acceptances.append(target['push_id'])
        # Model acceptance followed by cancellation before receipt persistence.
        raise asyncio.CancelledError('Synthetic interruption before receipt')

    monkeypatch.setattr(notifier, 'send_push', accepted_but_interrupted)
    with pytest.raises(asyncio.CancelledError):
        await notifier.deliver_event_notification(job)
    event_id = UUID(job['match_event_id'])
    assert await db.pool().fetchval('SELECT count(*) FROM notification_deliveries WHERE event_id=$1', event_id) == 0
    retry = AsyncMock(return_value='accepted')
    monkeypatch.setattr(notifier, 'send_push', retry)
    await notifier.deliver_event_notification(job)
    retry.assert_awaited_once()
    assert retry.await_args.args[0]['push_id'] == provider_acceptances[0]
    # The retry is necessary: the database cannot distinguish a lost acceptance
    # from a request that never reached the provider. Exactly-once is impossible.


@pytest.mark.parametrize('kind', ['goal', 'card', 'kickoff'])
async def test_existing_receipt_without_event_completion_prevents_resend(fixtures, monkeypatch, kind):
    job = await job_for(fixtures['match'], kind)
    event_id = UUID(job['match_event_id'])
    push_id = await db.pool().fetchval(
        'SELECT id FROM push_subscriptions WHERE user_id=$1', fixtures['user']['id'],
    )
    await db.execute('INSERT INTO notification_deliveries (event_id,push_id) VALUES ($1,$2)', event_id, push_id)
    send = AsyncMock(return_value='accepted')
    monkeypatch.setattr(notifier, 'send_push', send)
    await notifier.deliver_event_notification(job)
    await notifier.deliver_event_notification(job)
    send.assert_not_awaited()
    assert await db.pool().fetchval('SELECT delivered_at IS NOT NULL FROM match_events WHERE id=$1', event_id)


@pytest.mark.parametrize('kind', ['goal', 'card', 'kickoff'])
async def test_concurrent_workers_send_each_device_once(fixtures, monkeypatch, kind):
    await push_repo.upsert_push_subscription(
        fixtures['user']['id'], f'https://push.example/concurrent-{uuid4()}', 'key', 'auth',
    )
    job = await job_for(fixtures['match'], kind)
    calls = []

    async def send(target, _payload):
        calls.append(target['push_id'])
        await asyncio.sleep(0)
        return 'accepted'

    monkeypatch.setattr(notifier, 'send_push', send)
    await asyncio.gather(*(notifier.deliver_event_notification(job) for _ in range(4)))
    assert len(calls) == 2 and len(set(calls)) == 2
    assert await db.pool().fetchval(
        'SELECT count(*) FROM notification_deliveries WHERE event_id=$1', UUID(job['match_event_id']),
    ) == 2
