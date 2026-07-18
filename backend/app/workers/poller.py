"""Polling worker. Every tick: for each subscribed, non-finished match, fetch
fresh state, diff against the last-known state, gate each event through the
idempotency ledger, and enqueue the fresh ones for the notifier.

Run standalone:  python -m app.workers.poller
"""

from __future__ import annotations

import asyncio
import time

from arq.connections import ArqRedis

from app import db
from app.config import settings
from app.logging_conf import get_logger
from app.queue import enqueue_match_event, get_queue
from app.redis_client import close_redis, get_match_state, set_match_state
from app.repositories import match_events as events_repo
from app.repositories import matches as matches_repo
from app.repositories import teams as teams_repo
from app.sports_api import normalize
from app.sports_api.client import SportsApiClient
from app.workers.dedup_key import dedup_key
from app.workers.diff import diff_match
from app.workers.discovery import discover

log = get_logger("poller")


async def poll_match(client: SportsApiClient, queue: ArqRedis, match) -> None:
    raw = await client.get_live_fixture(match["external_id"])
    fixtures = normalize.normalize_fixtures(raw)
    if not fixtures:
        return
    fixture = fixtures[0]

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
            team = await teams_repo.upsert_team(external_id=ext_id, name=ext_id)
            key = f"{dedup_key(fixture['external_id'], event)}:t{ext_id}"
            recorded = await events_repo.record_event_if_new(
                match["id"], team["id"], event["type"], event, key
            )
            if recorded is None:
                continue  # already handled — no duplicate notification
            await enqueue_match_event(
                queue,
                {
                    "match_event_id": str(recorded["id"]),
                    "match_id": str(match["id"]),
                    "team_id": str(team["id"]),
                    "type": event["type"],
                    "detail": event,
                },
            )
            log.info("event enqueued: %s for team %s", event["type"], ext_id)

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


async def tick(client: SportsApiClient, queue: ArqRedis) -> None:
    matches = await matches_repo.find_pollable_matches()
    if not matches:
        log.debug("no subscribed matches to poll")
        return
    log.debug("polling %d matches", len(matches))
    for match in matches:
        try:
            await poll_match(client, queue, match)
        except Exception as exc:  # noqa: BLE001 - one bad match shouldn't kill the tick
            log.warning("poll failed for match %s: %s", match["id"], exc)


async def run() -> None:
    await db.connect()
    client = SportsApiClient()
    queue = await get_queue()
    log.info(
        "poller started (poll=%ss, discover=%ss)",
        settings.poll_interval_seconds,
        settings.discover_interval_seconds,
    )
    last_discover = 0.0
    try:
        while True:
            # Discover fixtures for subscribed teams on a slow cadence so the
            # poller always has real matches to watch, then poll the live ones.
            now = time.monotonic()
            if now - last_discover >= settings.discover_interval_seconds:
                try:
                    await discover(client)
                except Exception as exc:  # noqa: BLE001 - discovery must not kill the loop
                    log.warning("discovery pass failed: %s", exc)
                last_discover = now

            await tick(client, queue)
            await asyncio.sleep(settings.poll_interval_seconds)
    finally:
        await client.aclose()
        await close_redis()
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(run())
