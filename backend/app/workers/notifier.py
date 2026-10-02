"""Notification worker (arq). Consumes 'notify_match_event' jobs enqueued by the
poller, resolves the subscribed push targets for the event's team + type, and
sends Web Push. Pending events and per-recipient receipts survive failures.

Run:  arq app.workers.notifier.WorkerSettings
"""

from __future__ import annotations

from typing import Any, ClassVar
from uuid import UUID

from app import db
from app.logging_conf import get_logger
from app.queue import redis_settings
from app.redis_client import close_redis
from app.repositories import subscriptions as subs_repo
from app.workers.web_push import send_push

log = get_logger("notifier")


def _build_notification(etype: str, detail: dict[str, Any]) -> dict[str, str]:
    score = f"{detail.get('home_score', '')}–{detail.get('away_score', '')}".strip()
    if etype == "goal":
        body = (
            f"{detail['player']} ({detail.get('minute')}')  {score}"
            if detail.get("player")
            else f"Score: {score}"
        )
        return {"title": "⚽ GOAL!", "body": body}
    if etype == "card":
        red = (detail.get("detail") or "").find("Red") >= 0
        minute = f"({detail.get('minute')}')" if detail.get("minute") else ""
        return {
            "title": "\U0001f7e5 Red card" if red else "\U0001f7e8 Card",
            "body": f"{detail.get('player') or ''} {minute}".strip(),
        }
    if etype == "kickoff":
        return {"title": "\U0001f7e2 Kick-off", "body": "The match has started."}
    if etype == "full_time":
        return {"title": "\U0001f3c1 Full-time", "body": f"Final score: {score}"}
    return {"title": "Match update", "body": ""}


async def deliver_event_notification(payload: dict[str, Any]) -> None:
    """Core delivery: resolve subscribers for the event's team + type, send Web
    Push. Shared by the arq worker (queued path) and the cron poll endpoint
    (direct-call path, used where there's no persistent arq worker to consume
    a queue — e.g. Vercel serverless)."""
    # Serialize delivery of the same event across cron and worker processes.
    event_id = UUID(payload["match_event_id"])
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            event = await conn.fetchrow(
                "SELECT delivered_at FROM match_events WHERE id=$1 FOR UPDATE", event_id
            )
            if event is None or event["delivered_at"] is not None:
                return
            targets = await subs_repo.find_push_targets_for_event(
                payload["team_id"], payload["type"], payload["match_id"]
            )
            note = _build_notification(payload["type"], payload["detail"])
            note.update(icon="/icon.png", tag=str(event_id))
            failure = None
            for target in targets:
                sent = await conn.fetchval(
                    "SELECT 1 FROM notification_deliveries WHERE event_id=$1 AND push_id=$2",
                    event_id, target["push_id"],
                )
                if sent:
                    continue
                try:
                    await send_push(dict(target), note)
                    # Expired subscriptions may have been removed by send_push.
                    await conn.execute(
                        "INSERT INTO notification_deliveries (event_id, push_id) "
                        "SELECT $1, id FROM push_subscriptions WHERE id=$2 "
                        "ON CONFLICT DO NOTHING", event_id, target["push_id"],
                    )
                except Exception as exc:
                    failure = exc
                    log.warning("push failed for event %s: %s", event_id, exc, exc_info=True)
            if failure is None:
                await conn.execute(
                    "UPDATE match_events SET delivered_at=now() WHERE id=$1", event_id
                )
        # Commit successful recipients before propagating a partial failure.
        if failure is not None:
            raise failure


async def notify_match_event(ctx: dict, payload: dict[str, Any]) -> None:
    # Failures remain pending; the poller re-enqueues them on a later tick.
    await deliver_event_notification(payload)


async def _startup(ctx: dict) -> None:
    await db.connect()
    log.info("notifier started")


async def _shutdown(ctx: dict) -> None:
    await close_redis()
    await db.disconnect()


class WorkerSettings:
    functions: ClassVar[list] = [notify_match_event]
    on_startup = _startup
    on_shutdown = _shutdown
    redis_settings = redis_settings()
    # Leave pool capacity for recipient lookups and expired-subscription cleanup.
    max_jobs = 5
    max_tries = 5
    keep_result = 0
