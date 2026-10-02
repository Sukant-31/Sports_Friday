"""Polling worker. Every tick: for each subscribed, non-finished match, fetch
fresh state, diff against the last-known state, gate each event through the
idempotency ledger, and enqueue the fresh ones for the notifier.

Run standalone:  python -m app.workers.poller
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app import db
from app.config import settings
from app.logging_conf import get_logger
from app.polling_schema import ensure_polling_schema
from app.queue import enqueue_match_event, get_queue
from app.redis_client import close_redis, get_match_state, set_match_state
from app.repositories import match_events as events_repo
from app.repositories import matches as matches_repo
from app.sports_api import normalize
from app.sports_api.client import ApiRequestSkipped, SportsApiClient
from app.workers.dedup_key import dedup_key
from app.workers.diff import diff_match
from app.workers.discovery import discover
from app.workers.run_report import RunReport
from app.workers.polling_policy import decide

log = get_logger("poller")

Notify = Callable[[dict[str, Any]], Awaitable[None]]


async def poll_match(client: SportsApiClient, notify: Notify, match) -> None:
    raw = await client.get_live_fixture(match["external_id"])
    fixtures = normalize.normalize_fixtures(raw)
    if not fixtures:
        return
    fixture = fixtures[0]
    await process_fixture(notify, match, fixture)


async def process_fixture(notify: Notify, match, fixture, *, report=None) -> None:
    team_ids = {
        fixture["home_external_id"]: match["home_team_id"],
        fixture["away_external_id"]: match["away_team_id"],
    }

    # Baseline: Redis cache, else rehydrate from the persisted row so a cache
    # miss doesn't replay history as new events.
    prev = await get_match_state(str(match["id"])) or {
        "status": match["status"],
        "home_score": match["home_score"],
        "away_score": match["away_score"],
        "home_external_id": fixture["home_external_id"],
        "away_external_id": fixture["away_external_id"],
        "events": [],
    }

    for event in diff_match(prev, fixture):
        team_ext_ids = (
            [event["team_external_id"]]
            if event.get("team_external_id")
            else [fixture["home_external_id"], fixture["away_external_id"]]
        )
        for ext_id in team_ext_ids:
            team_id = team_ids.get(ext_id)
            if team_id is None:
                log.warning("event references unknown team %s in match %s", ext_id, match["id"])
                continue
            key = f"{dedup_key(fixture['external_id'], event)}:t{ext_id}"
            recorded = await events_repo.record_event_if_new(
                match["id"], team_id, event["type"], event, key
            )
            if recorded is None:
                continue  # already handled — no duplicate notification
            if report is not None:
                report.notifications_generated += 1
            await notify(
                {
                    "match_event_id": str(recorded["id"]),
                    "match_id": str(match["id"]),
                    "team_id": str(team_id),
                    "type": event["type"],
                    "detail": event,
                }
            )
            log.info("event notified: %s for team %s", event["type"], ext_id)

    next_state = {
        "status": fixture["status"],
        "home_score": fixture["home_score"],
        "away_score": fixture["away_score"],
        "home_external_id": fixture["home_external_id"],
        "away_external_id": fixture["away_external_id"],
        "events": fixture["events"],
    }
    await set_match_state(str(match["id"]), next_state)
    await matches_repo.update_match_state(
        match["id"],
        fixture["status"],
        fixture["home_score"],
        fixture["away_score"],
        fixture.get("minute"),
    )


async def tick(client: SportsApiClient, notify: Notify, *, report: RunReport | None = None) -> None:
    matches = await matches_repo.find_pollable_matches()
    now = datetime.now(timezone.utc)
    due = [(match, decision) for match in matches if (decision := decide(match, now))]
    due.sort(key=lambda item: {'high': 0, 'medium': 1, 'low': 2}[item[1].priority])
    if report is not None:
        report.fixtures_requiring_polling = len(due)
        report.fixture_priorities = {level: sum(d.priority == level for _, d in due)
                                    for level in ('high', 'medium', 'low')}
    live = {}
    live_needed = any(d.reason in ('live', 'active_window') for _, d in due)
    live_error = None
    if live_needed:
        try:
            live = {fx['external_id']: fx for fx in normalize.normalize_fixtures(
                await client.get_live_fixtures())}
        except ApiRequestSkipped:
            live_error = 'skipped'
        except Exception as exc:
            live_error = exc
            log.warning('live feed failed error_type=%s', type(exc).__name__)
            if report is not None:
                report.record('polling', 'live_feed', exc)
    dates = {}
    for match, decision in due:
        try:
            fixture = live.get(match['external_id'])
            if fixture is None:
                if live_error is not None and decision.reason in ('live', 'active_window'):
                    continue
                day = (match['starts_at'] or now).date().isoformat()
                # One shared date feed captures pre-match/final status when a
                # fixture is absent from live=all. Never fetch per team/match.
                key = (day, decision.priority)
                if key not in dates:
                    try:
                        dates[key] = {fx['external_id']: fx for fx in normalize.normalize_fixtures(
                            await client.get_date_fixtures(day, priority=decision.priority))}
                    except Exception as exc:
                        dates[key] = exc
                        raise
                if isinstance(dates[key], Exception):
                    continue
                fixture = dates[key].get(match['external_id'])
            if fixture is not None:
                await process_fixture(notify, match, fixture, report=report)
            else:
                await matches_repo.mark_checked(match['id'])
            if report is not None:
                report.record('polling', match['id'])
        except ApiRequestSkipped:
            continue
        except Exception as exc:  # noqa: BLE001 - one bad match shouldn't kill the tick
            if report is not None:
                report.record('polling', match['id'], exc)
            log.warning("poll failed for match %s: %s", match["id"], exc)

    # Retry durable pending events even after a match has finished, or enqueue failed.
    for event in await events_repo.pending_events():
        try:
            await notify({
                "match_event_id": str(event["id"]),
                "match_id": str(event["match_id"]),
                "team_id": str(event["team_id"]),
                "type": event["type"], "detail": event["detail"],
            })
        except Exception as exc:
            log.warning("pending notification failed for %s: %s", event["id"], exc)


async def run() -> None:
    """Standalone long-running poller (docker/local/Render). Enqueues events
    onto the arq queue for a separate notifier worker to consume."""
    await db.connect()
    await ensure_polling_schema()
    client = SportsApiClient()
    queue = await get_queue()

    async def notify(payload: dict) -> None:
        await enqueue_match_event(queue, payload)

    log.info(
        "poller started (poll=%ss, discover=%ss)",
        settings.poll_interval_seconds,
        settings.discover_interval_seconds,
    )
    last_discover = 0.0
    try:
        while True:
            await tick(client, notify)
            # Discover fixtures for subscribed teams on a slow cadence so the
            # poller always has real matches to watch, then poll the live ones.
            now = time.monotonic()
            if now - last_discover >= settings.discover_interval_seconds:
                try:
                    await discover(client)
                except Exception as exc:  # noqa: BLE001 - discovery must not kill the loop
                    log.warning("discovery pass failed: %s", exc)
                last_discover = now
                await tick(client, notify)
            await asyncio.sleep(settings.poll_interval_seconds)
    finally:
        await client.aclose()
        await close_redis()
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(run())
