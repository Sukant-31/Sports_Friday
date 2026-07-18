"""Integration test for the per-match detail query. Requires Postgres; skips
without it. Assumes migrations applied."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app import db
from app.redis_client import redis
from app.repositories import match_events as E
from app.repositories import matches as M
from app.repositories import subscriptions as S
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


async def test_detail_scoped_to_follower_with_full_timeline(infra):
    rid = uuid.uuid4().hex[:8]
    home = await T.upsert_team("100", "Home FC", "Demo League")
    away = await T.upsert_team("200", "Away United", "Demo League")
    follower = await U.create_user(f"follower-{rid}@ex.com", "hash")
    stranger = await U.create_user(f"stranger-{rid}@ex.com", "hash")
    match = await db.fetchrow(
        "INSERT INTO matches (external_id,home_team_id,away_team_id,status,home_score,away_score,minute) "
        "VALUES ($1,$2,$3,'live',1,0,30) RETURNING *",
        f"detail-{rid}", home["id"], away["id"],
    )
    await S.create_subscription(follower["id"], home["id"], True, True, True)
    await E.record_event_if_new(match["id"], home["id"], "kickoff",
        {"minute": 0, "detail": "Kick-off"}, f"detail-{rid}:kickoff")
    await E.record_event_if_new(match["id"], home["id"], "goal",
        {"minute": 23, "player": "Saka", "home_score": 1, "away_score": 0}, f"detail-{rid}:g1")

    try:
        # Follower sees the match with its full timeline...
        m = await M.find_match_for_user(follower["id"], match["id"])
        assert m is not None
        assert m["home_team"] == "Home FC" and m["minute"] == 30
        events = await M.find_all_events_for_match(match["id"])
        assert [e["type"] for e in events] == ["kickoff", "goal"]  # oldest first

        # ...a non-follower does not (endpoint would 404).
        assert await M.find_match_for_user(stranger["id"], match["id"]) is None
    finally:
        await db.execute("DELETE FROM users WHERE id = ANY($1::uuid[])",
                         [follower["id"], stranger["id"]])
        await db.execute("DELETE FROM matches WHERE id = $1", match["id"])
