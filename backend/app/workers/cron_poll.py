"""One-shot DB-first polling for serverless, with direct existing push delivery."""
from __future__ import annotations

from uuid import UUID

from app import db
from app.config import settings
from app.logging_conf import get_logger
from app.notification_schema import ensure_notification_delivery_schema
from app.polling_schema import ensure_polling_schema
from app.repositories import matches as matches_repo
from app.repositories import sports_requests
from app.workers.discovery import discover
from app.workers.notifier import deliver_event_notification
from app.workers.poller import tick
from app.workers.run_report import RunReport
from app.sports_api.client import SportsApiClient

log = get_logger('cron_poll')


async def _receipt_count(event_id: str) -> int:
    row = await db.fetchrow('SELECT count(*)::int AS n FROM notification_deliveries WHERE event_id=$1',
                            UUID(event_id))
    return row['n']


async def run_once() -> dict:
    client = SportsApiClient()
    report = RunReport()
    discovered = 0

    async def notify(payload):
        before = await _receipt_count(payload['match_event_id'])
        try:
            await deliver_event_notification(payload)
        except Exception as exc:
            report.record('notifications', payload['match_event_id'], exc)
            raise
        else:
            report.record('notifications', payload['match_event_id'])
        finally:
            # Count committed recipient receipts, including partial successes.
            # Acceptance by the push service is not proof of browser display.
            report.notifications_delivered += max(0, await _receipt_count(
                payload['match_event_id']) - before)

    try:
        await ensure_notification_delivery_schema()
        await ensure_polling_schema()
        async with sports_requests.lock(client.scope, 'cron') as held:
            if not held:
                report.skip_reason = 'overlapping_run'
            else:
                counts = await matches_repo.polling_counts()
                report.stored_fixtures = counts['stored_fixtures']
                report.live_fixtures = counts['live_fixtures']
                # Spend on existing active fixtures before discovery/name repair.
                await tick(client, notify, report=report)
                try:
                    discovered = await discover(client, report=report)
                except Exception as exc:
                    report.record('discovery', 'pass', exc)
                    log.warning('discovery pass failed error_type=%s', type(exc).__name__)
                # Newly discovered live/near-kickoff fixtures need not wait for
                # the next cron. Shared feeds and checked_at prevent duplicates.
                if report.fixtures_upserted:
                    previous_due = report.fixtures_requiring_polling
                    await tick(client, notify, report=report)
                    report.fixtures_requiring_polling += previous_due
            report.api_budget = await sports_requests.budget(client.scope, settings.max_daily_api_requests)
    except Exception as exc:
        report.record('database', 'cron_state', exc)
        log.exception('cron state processing failed')
    finally:
        report.api_requests = client.stats.copy()
        await client.aclose()
    result = report.response(max(discovered, report.fixtures_upserted))
    log.info('cron run summary: %s', result)
    return result
