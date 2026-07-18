"""Integration test for fixture discovery over mock data. Requires Postgres;
skips cleanly without it. Assumes migrations applied."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app import db
from app.redis_client import redis
from app.repositories import matches as matches_repo
from app.repositories import subscriptions as subs_repo
from app.repositories import teams as teams_repo
from app.repositories import users as users_repo
from app.sports_api.mock import MockSportsApiClient, build_timeline
from app.workers.discovery import discover


@pytest_asyncio.fixture
async def infra():
    try:
        await db.connect()
        await redis.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Postgres/Redis not available")
    yield
    await db.disconnect()


async def test_discovery_upserts_fixtures_for_subscribed_teams(infra):
    run_id = uuid.uuid4().hex[:8]
    match_ext = f"disc-{run_id}"
    # A user follows the mock home team (external id "100").
    home = await teams_repo.upsert_team("100", "Home FC", "Demo League")
    user = await users_repo.create_user(f"disc-{run_id}@example.com", "hash")
    await subs_repo.create_subscription(user["id"], home["id"], True, True, True)

    client = MockSportsApiClient(build_timeline(match_ext))
    try:
        count = await discover(client)
        assert count >= 1

        # The upcoming fixture should now exist as a pollable match.
        match = await matches_repo.find_match_by_external_id(match_ext)
        assert match is not None
        assert match["status"] == "scheduled"
        assert match["starts_at"] is not None

        # And it shows up in the poller's work set (team is subscribed).
        pollable_ids = {m["external_id"] for m in await matches_repo.find_pollable_matches()}
        assert match_ext in pollable_ids
    finally:
        await db.execute("DELETE FROM users WHERE id = $1", user["id"])
        await db.execute("DELETE FROM matches WHERE external_id = $1", match_ext)
