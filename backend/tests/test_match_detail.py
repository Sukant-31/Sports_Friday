"""Integration test for the per-match detail query. Requires Postgres; skips
without it. Assumes migrations applied."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

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


async def test_dashboard_retention_polling_window_and_distinct_goals(infra):
    rid = uuid.uuid4().hex[:8]
    home = await T.upsert_team(f"home-{rid}", "Home FC", "Demo League")
    away = await T.upsert_team(f"away-{rid}", "Away United", "Demo League")
    user = await U.create_user(f"dashboard-{rid}@example.com", "hash")
    match_ids = []
    now = datetime.now(timezone.utc)
    try:
        await S.create_subscription(user["id"], home["id"], True, True, True)
        fixtures = {}
        for label, state, start, polled in (
            ("near", "scheduled", now + timedelta(minutes=5), None),
            ("far", "scheduled", now + timedelta(days=3), None),
            ("recent", "finished", now - timedelta(hours=2), now),
            ("old", "finished", now - timedelta(days=3), now - timedelta(days=2)),
            ("live", "live", now - timedelta(hours=1), now),
        ):
            row = await db.fetchrow(
                "INSERT INTO matches (external_id, home_team_id, away_team_id, status, "
                "starts_at, last_polled_at) VALUES ($1,$2,$3,$4,$5,$6) RETURNING *",
                f"{rid}-{label}", home["id"], away["id"], state, start, polled,
            )
            fixtures[label] = row
            match_ids.append(row["id"])
        visible = {r["id"] for r in await M.find_live_matches_for_user(user["id"])}
        assert fixtures["recent"]["id"] in visible
        assert fixtures["old"]["id"] not in visible
        pollable = {r["id"] for r in await M.find_pollable_matches()}
        assert fixtures["near"]["id"] in pollable
        assert fixtures["live"]["id"] in pollable
        assert fixtures["far"]["id"] not in pollable
        assert fixtures["recent"]["id"] not in pollable

        match_id = fixtures["live"]["id"]
        for index, team in enumerate((home, away, home)):
            await E.record_event_if_new(match_id, team["id"], "goal",
                {"minute": 23, "home_score": index + 1, "away_score": 0}, f"{rid}:goal:{index}")
        for team in (home, away):
            await E.record_event_if_new(match_id, team["id"], "kickoff",
                {"minute": 0}, f"{rid}:kickoff:{team['id']}")
        timeline = await M.find_all_events_for_match(match_id)
        assert sum(e["type"] == "goal" for e in timeline) == 3
        assert sum(e["type"] == "kickoff" for e in timeline) == 1
        recent = await M.find_recent_events_for_matches([match_id])
        assert sum(e["type"] == "goal" for e in recent) == 3
        assert sum(e["type"] == "kickoff" for e in recent) == 1

        # A fixture refresh preserves known metadata; a real name repairs an ID.
        await T.upsert_team(home["external_id"], home["external_id"])
        preserved = await T.find_team_by_id(home["id"])
        assert preserved["name"] == "Home FC" and preserved["league"] == "Demo League"
        await db.execute("UPDATE teams SET name=external_id WHERE id=$1", home["id"])
        await T.upsert_team(home["external_id"], "Home FC")
        assert (await T.find_team_by_id(home["id"]))["name"] == "Home FC"
    finally:
        await db.execute("DELETE FROM users WHERE id=$1", user["id"])
        await db.execute("DELETE FROM matches WHERE id=ANY($1::uuid[])", match_ids)
        await db.execute("DELETE FROM teams WHERE id=ANY($1::uuid[])", [home["id"], away["id"]])
