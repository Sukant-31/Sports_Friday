"""Integration test for per-match mute. Requires Postgres; skips without it."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app import db
from app.redis_client import redis
from app.repositories import muted_matches as muted_repo
from app.repositories import push_subscriptions as push_repo
from app.repositories import subscriptions as subs_repo
from app.repositories import teams as T
from app.repositories import users as U


@pytest_asyncio.fixture
async def infra():
    try:
        await db.connect()
        await redis.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Postgres/Redis not available")
    yield
    await db.disconnect()


async def test_mute_excludes_user_from_notification_targets(infra):
    rid = uuid.uuid4().hex[:8]
    home = await T.upsert_team("100", "Home FC", "Demo League")
    await T.upsert_team("200", "Away United", "Demo League")
    user = await U.create_user(f"mute-{rid}@ex.com", "hash")
    match = await db.fetchrow(
        "INSERT INTO matches (external_id,home_team_id,away_team_id,status) "
        "VALUES ($1,$2,(SELECT id FROM teams WHERE external_id='200'),'live') RETURNING *",
        f"mute-{rid}", home["id"],
    )
    await subs_repo.create_subscription(user["id"], home["id"], True, True, True)
    await push_repo.upsert_push_subscription(user["id"], f"https://p/{rid}", "p", "a")

    try:
        # Before muting: the user is a goal-notification target for this match.
        targets = await subs_repo.find_push_targets_for_event(home["id"], "goal", match["id"])
        assert any(t["user_id"] == user["id"] for t in targets)

        # After muting: excluded for this match…
        await muted_repo.mute(user["id"], match["id"])
        assert await muted_repo.is_muted(user["id"], match["id"]) is True
        targets = await subs_repo.find_push_targets_for_event(home["id"], "goal", match["id"])
        assert all(t["user_id"] != user["id"] for t in targets)

        # …but still a target for a *different* match (mute is per-match).
        other = await db.fetchrow(
            "INSERT INTO matches (external_id,home_team_id,away_team_id,status) "
            "VALUES ($1,$2,(SELECT id FROM teams WHERE external_id='200'),'live') RETURNING *",
            f"other-{rid}", home["id"],
        )
        targets = await subs_repo.find_push_targets_for_event(home["id"], "goal", other["id"])
        assert any(t["user_id"] == user["id"] for t in targets)

        # Unmute restores the target.
        await muted_repo.unmute(user["id"], match["id"])
        targets = await subs_repo.find_push_targets_for_event(home["id"], "goal", match["id"])
        assert any(t["user_id"] == user["id"] for t in targets)

        await db.execute("DELETE FROM matches WHERE id = $1", other["id"])
    finally:
        await db.execute("DELETE FROM users WHERE id = $1", user["id"])
        await db.execute("DELETE FROM matches WHERE id = $1", match["id"])
