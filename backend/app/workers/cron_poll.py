"""One-shot poll pass for serverless hosts with no persistent worker process
(e.g. Vercel Cron). Runs discovery + a single poll tick, delivering
notifications directly instead of enqueueing to arq — there's no separate
notifier process around to consume that queue."""

from __future__ import annotations

from app.logging_conf import get_logger
from app.sports_api.client import SportsApiClient
from app.workers.discovery import discover
from app.workers.notifier import deliver_event_notification
from app.workers.poller import tick

log = get_logger("cron_poll")


async def run_once() -> dict:
    client = SportsApiClient()
    try:
        try:
            discovered = await discover(client)
        except Exception as exc:  # noqa: BLE001 - discovery must not block polling
            log.warning("discovery pass failed: %s", exc)
            discovered = 0

        await tick(client, deliver_event_notification)
        return {"discovered": discovered}
    finally:
        await client.aclose()
