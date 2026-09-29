"""Integration test for the poller → notifier pipeline over mock data.

Requires Postgres + Redis (the CI services; locally via docker/podman). Skips
cleanly when neither is reachable. Assumes migrations have been applied.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app import db
from app.redis_client import get_match_state, redis
from app.repositories import push_subscriptions as push_repo
from app.repositories import subscriptions as subs_repo
from app.repositories import teams as teams_repo
from app.repositories import users as users_repo
from app.sports_api.mock import MockSportsApiClient, build_timeline
from app.workers import notifier as notifier_mod
from app.workers.poller import poll_match


class CapturingQueue:
    """Stand-in `notify` callable that records delivered payloads instead of
    enqueueing/sending them."""

    def __init__(self) -> None:
        self.jobs: list[dict] = []

    async def __call__(self, payload: dict) -> None:
        self.jobs.append(payload)


@pytest_asyncio.fixture
async def infra():
    try:
        await db.connect()
        await redis.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Postgres/Redis not available")
    yield
    await db.disconnect()


@pytest_asyncio.fixture
async def fixtures(infra):
    run_id = uuid.uuid4().hex[:8]
    home = await teams_repo.upsert_team("100", "Home FC", "Demo League")
    await teams_repo.upsert_team("200", "Away United", "Demo League")
    user = await users_repo.create_user(f"pytest-{run_id}@example.com", "hash")
    match = await db.fetchrow(
        """
        INSERT INTO matches (external_id, home_team_id, away_team_id, status)
        VALUES ($1, $2, (SELECT id FROM teams WHERE external_id='200'), 'scheduled')
        RETURNING *
        """,
        f"pytest-match-{run_id}",
        home["id"],
    )
    await subs_repo.create_subscription(user["id"], home["id"], True, True, True)
    await push_repo.upsert_push_subscription(
        user["id"], f"https://push.example/{run_id}", "p256dh", "auth"
    )
    yield {"user": user, "match": match}
    await db.execute("DELETE FROM users WHERE id = $1", user["id"])
    await db.execute("DELETE FROM matches WHERE id = $1", match["id"])
    await redis.delete(f"match:{match['id']}:state")


async def test_poller_detects_events_and_dedupes(fixtures):
    match = fixtures["match"]
    client = MockSportsApiClient(build_timeline(match["external_id"]))
    queue = CapturingQueue()

    per_snapshot: list[int] = []
    while True:
        before = len(queue.jobs)
        await poll_match(client, queue, match)
        per_snapshot.append(len(queue.jobs) - before)
        if not client.advance():
            break

    types = [j["type"] for j in queue.jobs]
    # 2 goals, 1 card detected; kickoff + full_time each fan out to both teams.
    assert types.count("goal") == 2
    assert types.count("card") == 1
    assert types.count("kickoff") == 2
    assert types.count("full_time") == 2

    # 5th snapshot is a duplicate re-poll — the ledger must yield zero new events.
    assert per_snapshot[4] == 0

    # State was persisted to Redis for the next tick's baseline.
    state = await get_match_state(str(match["id"]))
    assert state is not None and state["status"] == "finished"


async def test_notifier_sends_for_subscribed_team(fixtures, monkeypatch):
    match = fixtures["match"]
    client = MockSportsApiClient(build_timeline(match["external_id"]))
    queue = CapturingQueue()
    while True:
        await poll_match(client, queue, match)
        if not client.advance():
            break

    sent: list[tuple[str, dict]] = []

    async def fake_send_push(target, payload):
        sent.append((target["endpoint"], payload))

    monkeypatch.setattr(notifier_mod, "send_push", fake_send_push)

    # Deliver every enqueued job through the real notifier task.
    for job in queue.jobs:
        await notifier_mod.notify_match_event({}, job)

    titles = [p["title"] for _, p in sent]
    # Home team is followed → goals, card, kickoff and full-time all delivered.
    assert any("GOAL" in t for t in titles)
    assert any("Full-time" in t for t in titles)
    # Away-team lifecycle jobs resolve to no push target, so nothing extra fires.
    assert all(e.startswith("https://push.example/") for e, _ in sent)


async def test_failed_delivery_retried_after_match_finishes(fixtures, monkeypatch):
    from app.repositories import match_events as events_repo
    from app.workers.poller import tick

    match = fixtures["match"]
    queue = CapturingQueue()
    client = MockSportsApiClient(build_timeline(match["external_id"]))
    while True:
        await poll_match(client, queue, match)
        if not client.advance():
            break

    goal = next(j for j in queue.jobs if j["type"] == "goal")
    async def fail(target, payload):
        raise RuntimeError("push temporarily unavailable")
    monkeypatch.setattr(notifier_mod, "send_push", fail)
    with pytest.raises(RuntimeError):
        await notifier_mod.deliver_event_notification(goal)
    assert any(str(e["id"]) == goal["match_event_id"] for e in await events_repo.pending_events())

    sent = []
    async def succeed(target, payload):
        sent.append(payload)
    monkeypatch.setattr(notifier_mod, "send_push", succeed)
    await tick(client, notifier_mod.deliver_event_notification)
    assert any(p["tag"] == goal["match_event_id"] for p in sent)
    count = len(sent)
    await notifier_mod.deliver_event_notification(goal)
    assert len(sent) == count


async def test_partial_failure_does_not_resend_successful_recipient(fixtures, monkeypatch):
    match = fixtures["match"]
    second_endpoint = f"https://push.example/second-{uuid.uuid4()}"
    await push_repo.upsert_push_subscription(
        fixtures["user"]["id"], second_endpoint, "p256dh", "auth"
    )
    queue = CapturingQueue()
    client = MockSportsApiClient(build_timeline(match["external_id"]))
    await poll_match(client, queue, match)
    client.advance()
    await poll_match(client, queue, match)
    job = next(j for j in queue.jobs if j["team_id"] == str(match["home_team_id"]))
    successes = []
    broken = True

    async def send(target, payload):
        if target["endpoint"] == second_endpoint and broken:
            raise RuntimeError("one device unavailable")
        successes.append(target["endpoint"])

    monkeypatch.setattr(notifier_mod, "send_push", send)
    with pytest.raises(RuntimeError):
        await notifier_mod.deliver_event_notification(job)
    assert len(successes) == 1
    broken = False
    await notifier_mod.deliver_event_notification(job)
    assert len(successes) == 2
    assert len(set(successes)) == 2
