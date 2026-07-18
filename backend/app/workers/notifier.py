"""Notification worker (arq). Consumes 'notify_match_event' jobs enqueued by the
poller, resolves the subscribed push targets for the event's team + type, and
sends Web Push. Because the poller gates enqueue through the match_events
ledger, arq retries can't multiply distinct notifications.

Run:  arq app.workers.notifier.WorkerSettings
"""

from __future__ import annotations

import asyncio
from typing import Any

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


async def notify_match_event(ctx: dict, payload: dict[str, Any]) -> None:
    team_id = payload["team_id"]
    etype = payload["type"]
    detail = payload["detail"]

    targets = await subs_repo.find_push_targets_for_event(team_id, etype)
    if not targets:
        return

    note = _build_notification(etype, detail)
    note["icon"] = "/icon.png"

    results = await asyncio.gather(
        *(send_push(dict(t), note) for t in targets), return_exceptions=True
    )
    failures = [r for r in results if isinstance(r, Exception)]
    if failures:
        # Raise so arq retries with backoff (dedup ledger prevents re-emitting on
        # a later poll, so retries can't create distinct notifications).
        log.warning("%d push(es) failed for %s", len(failures), etype)
        raise failures[0]
    log.info("sent %d notifications for %s", len(targets), etype)


async def _startup(ctx: dict) -> None:
    await db.connect()
    log.info("notifier started")


async def _shutdown(ctx: dict) -> None:
    await close_redis()
    await db.disconnect()


class WorkerSettings:
    functions = [notify_match_event]
    on_startup = _startup
    on_shutdown = _shutdown
    redis_settings = redis_settings()
    max_tries = 5
