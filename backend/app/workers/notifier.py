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
from app.notification_schema import ensure_notification_delivery_schema
from app.queue import redis_settings
from app.redis_client import close_redis
from app.repositories import subscriptions as subs_repo
from app.workers.web_push import InvalidPushSubscription, PermanentPushError, send_push

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
    event_id = UUID(payload["match_event_id"])
    lifecycle = payload["type"] not in ("goal", "card")
    async with db.pool().acquire() as conn:
        event = await conn.fetchrow(
            "SELECT delivered_at FROM match_events WHERE id=$1", event_id
        )
        if event is None or event["delivered_at"] is not None:
            return
        targets = await subs_repo.find_push_targets_for_event(
            payload["team_id"], payload["type"], payload["match_id"]
        )
        note = _build_notification(payload["type"], payload["detail"])
        note.update(icon="/icon.png", tag=str(event_id))
        failure = None
        terminal_failure = None
        for target in targets:
            # Commit each device's outcome before attempting another device.
            # Hold the lock through the send: an unlocked check-then-send races.
            async with conn.transaction():
                if lifecycle:
                    await conn.fetchrow(
                        "SELECT id FROM matches WHERE id=$1 FOR UPDATE", UUID(payload["match_id"])
                    )
                event = await conn.fetchrow(
                    "SELECT delivered_at FROM match_events WHERE id=$1 FOR UPDATE", event_id
                )
                if event is None or event["delivered_at"] is not None:
                    continue
                if lifecycle:
                    # Reuse existing receipts, including those written before
                    # this fix; preserve each team's recipient/preference checks.
                    sent = await conn.fetchval(
                        "SELECT 1 FROM (SELECT event_id,push_id FROM notification_deliveries "
                        "UNION ALL SELECT event_id,push_id FROM notification_terminal_outcomes) nd "
                        "JOIN match_events me ON me.id=nd.event_id "
                        "WHERE me.match_id=$1 AND me.type=$2 AND nd.push_id=$3 LIMIT 1",
                        UUID(payload["match_id"]), payload["type"], target["push_id"],
                    )
                else:
                    sent = await conn.fetchval(
                        "SELECT 1 FROM (SELECT event_id,push_id FROM notification_deliveries "
                        "UNION ALL SELECT event_id,push_id FROM notification_terminal_outcomes) nd "
                        "WHERE event_id=$1 AND push_id=$2 LIMIT 1",
                        event_id, target["push_id"],
                    )
                if sent:
                    continue
                try:
                    outcome = await send_push(dict(target), note)
                    if outcome == "expired":
                        continue
                    if outcome == "simulated":
                        await _record_terminal(conn, event_id, target["push_id"], "simulated")
                        continue
                    if outcome != "accepted":
                        raise RuntimeError("Unknown push transport outcome")
                    # Expired subscriptions may have been removed by send_push.
                    await conn.execute(
                        "INSERT INTO notification_deliveries (event_id, push_id) "
                        "SELECT $1, id FROM push_subscriptions WHERE id=$2 "
                        "ON CONFLICT DO NOTHING", event_id, target["push_id"],
                    )
                except (PermanentPushError, InvalidPushSubscription) as exc:
                    await _record_terminal(conn, event_id, target["push_id"], "permanent_failure",
                                           getattr(exc, "status_code", None))
                    terminal_failure = exc
                    log.warning("push permanently rejected event=%s error_type=%s status=%s",
                                event_id, type(exc).__name__, getattr(exc, "status_code", None))
                except Exception as exc:  # noqa: BLE001 — preserve retryable errors for re-raise
                    failure = exc
                    log.warning("push retryable failure event=%s error_type=%s", event_id, type(exc).__name__)
        if failure is None:
            # All selected devices have accepted/terminal outcomes. This is
            # processing completion, never confirmation of browser display.
            async with conn.transaction():
                await conn.fetchrow("SELECT id FROM match_events WHERE id=$1 FOR UPDATE", event_id)
                await conn.execute(
                    "UPDATE match_events SET delivered_at=now() WHERE id=$1 AND delivered_at IS NULL",
                    event_id,
                )
        if failure is not None:
            raise failure
        if terminal_failure is not None:
            raise terminal_failure


async def _record_terminal(conn, event_id, push_id, outcome, status_code=None):
    await conn.execute(
        "INSERT INTO notification_terminal_outcomes (event_id,push_id,outcome,status_code) "
        "SELECT $1,id,$3,$4 FROM push_subscriptions WHERE id=$2 ON CONFLICT DO NOTHING",
        event_id, push_id, outcome, status_code,
    )


async def notify_match_event(ctx: dict, payload: dict[str, Any]) -> None:
    # Failures remain pending; the poller re-enqueues them on a later tick.
    await deliver_event_notification(payload)


async def _startup(ctx: dict) -> None:
    await db.connect()
    await ensure_notification_delivery_schema()
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
