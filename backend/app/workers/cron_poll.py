"""One-shot poll pass for serverless hosts with no persistent worker process
(e.g. Vercel Cron). Runs discovery + a single poll tick, delivering
notifications directly instead of enqueueing to arq — there's no separate
notifier process around to consume that queue."""

from __future__ import annotations

from app.logging_conf import get_logger
from app.notification_schema import ensure_notification_delivery_schema
from app.sports_api.client import SportsApiClient
from app.workers.discovery import discover
from app.workers.notifier import deliver_event_notification
from app.workers.poller import tick
from app.workers.run_report import RunReport

log = get_logger("cron_poll")


async def run_once() -> dict:
    client = SportsApiClient()
    report = RunReport()

    async def notify(payload):
        try:
            await deliver_event_notification(payload)
        except Exception as exc:
            report.record('notifications', payload['match_event_id'], exc)
            raise
        report.record('notifications', payload['match_event_id'])

    try:
        await ensure_notification_delivery_schema()
        try:
            discovered = await discover(client, report=report)
        except Exception as exc:  # noqa: BLE001 - discovery must not block polling
            log.warning("discovery pass failed: %s", exc)
            report.record('discovery', 'pass', exc)
            discovered = report.fixtures_upserted

        try:
            await tick(client, notify, report=report)
        except Exception:
            log.exception("cron polling failed after fixture discovery")
            raise
        result = report.response(max(discovered, report.fixtures_upserted))
        log.info('cron run summary: %s', result)
        return result
    finally:
        await client.aclose()
