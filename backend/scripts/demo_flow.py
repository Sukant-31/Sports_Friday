"""End-to-end demo of the poller → queue → notifier flow using mock match data.

Exercises the REAL pipeline — poll_match (diff + dedup ledger + arq enqueue),
a real arq burst worker, and the real notifier task — against a live Postgres +
Redis. Push delivery uses the console transport, so no browser is needed.

Prerequisites: Postgres + Redis running, migrations applied. Run:

    PUSH_TRANSPORT=console python scripts/demo_flow.py

Creates a temporary user/match/subscription, prints a timeline of detected
events and delivered notifications, then cleans up after itself.
"""

from __future__ import annotations

import asyncio
import uuid

from arq.worker import Worker

from app import db
from app.config import settings
from app.queue import get_queue, redis_settings
from app.redis_client import close_redis, redis
from app.repositories import push_subscriptions as push_repo
from app.repositories import subscriptions as subs_repo
from app.repositories import teams as teams_repo
from app.repositories import users as users_repo
from app.sports_api.mock import MockSportsApiClient, build_timeline
from app.workers.notifier import notify_match_event
from app.workers.poller import poll_match

BANNER = "=" * 64


async def _setup(run_id: str):
    home = await teams_repo.upsert_team("100", "Home FC", "Demo League")
    away = await teams_repo.upsert_team("200", "Away United", "Demo League")
    user = await users_repo.create_user(f"demo-{run_id}@example.com", "not-a-real-hash")
    match = await db.fetchrow(
        """
        INSERT INTO matches (external_id, home_team_id, away_team_id, status)
        VALUES ($1, $2, $3, 'scheduled')
        RETURNING *
        """,
        f"demo-match-{run_id}",
        home["id"],
        away["id"],
    )
    # Follow the home team, all notification types on.
    await subs_repo.create_subscription(user["id"], home["id"], True, True, True)
    # A fake browser push subscription (console transport won't actually POST it).
    await push_repo.upsert_push_subscription(
        user["id"], f"https://push.example/{run_id}", "p256dh-demo", "auth-demo"
    )
    return user, match


async def _teardown(user, match):
    await db.execute("DELETE FROM users WHERE id = $1", user["id"])  # cascades subs + push
    await db.execute("DELETE FROM matches WHERE id = $1", match["id"])  # cascades match_events
    await redis.delete(f"match:{match['id']}:state")


async def main() -> None:
    # Force console delivery for the demo regardless of .env.
    settings.push_transport = "console"

    await db.connect()
    run_id = uuid.uuid4().hex[:8]
    user, match = await _setup(run_id)
    client = MockSportsApiClient(build_timeline(match["external_id"]))
    queue = await get_queue()

    print(f"\n{BANNER}\n POLLER — feeding {len(client.labels())} mock snapshots\n{BANNER}")
    while True:
        before = await _queued_count(queue)
        await poll_match(client, queue, match)
        after = await _queued_count(queue)
        enqueued = after - before
        print(f"  [{client.label}]  → {enqueued} event(s) enqueued")
        if not client.advance():
            break

    print(f"\n{BANNER}\n NOTIFIER — draining the queue (console push)\n{BANNER}")
    worker = Worker(
        functions=[notify_match_event],
        redis_settings=redis_settings(),
        burst=True,
        handle_signals=False,
        poll_delay=0.1,
    )
    await worker.async_run()
    await worker.close()

    print(f"\n{BANNER}\n cleaning up demo data\n{BANNER}")
    await _teardown(user, match)
    await client.aclose()
    await close_redis()
    await db.disconnect()
    print("done.\n")


async def _queued_count(queue) -> int:
    # arq keeps pending jobs on a sorted set named 'arq:queue' by default.
    return await queue.zcard("arq:queue")


if __name__ == "__main__":
    asyncio.run(main())
