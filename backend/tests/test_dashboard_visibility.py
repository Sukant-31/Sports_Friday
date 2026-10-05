"""Dashboard visibility changes reads only; historical data survives."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from app import db
from app.config import settings
from app.main import create_app
from app.repositories import match_events, matches, muted_matches, subscriptions, teams, users
from app.security import create_token

INACTIVE = ["scheduled", "time_to_be_defined", "suspended", "interrupted", "postponed",
            "cancelled", "abandoned", "awarded", "walkover", "unknown", "unrecognized"]


@pytest_asyncio.fixture
async def dashboard():
    await db.connect()
    token = uuid4().hex
    team_ids, user_ids, match_ids = [], [], []
    try:
        for side in ("home", "away", "other"):
            team = await teams.upsert_team(f"visibility-{token}-{side}", side)
            team_ids.append(team["id"])
        for side in ("follower", "stranger"):
            user = await users.create_user(f"visibility-{token}-{side}@example.com", "test-hash")
            user_ids.append(user["id"])
        await subscriptions.create_subscription(user_ids[0], team_ids[0])
        now = await db.pool().fetchval("SELECT now()")

        async def insert(status="scheduled", start=None, polled=None, checked=None, followed=True):
            row = await db.fetchrow(
                "INSERT INTO matches(external_id,home_team_id,away_team_id,status,starts_at,"
                "last_polled_at,last_checked_at) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *",
                f"visibility-{uuid4().hex}", team_ids[0] if followed else team_ids[2],
                team_ids[1] if followed else team_ids[2], status, start, polled, checked)
            match_ids.append(row["id"])
            return row

        app = create_app()
        app.state.sports_client = SimpleNamespace(get_team_fixtures=AsyncMock(),
                                                  get_live_fixtures=AsyncMock())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="https://test",
                                    cookies={settings.auth_cookie_name: create_token(str(user_ids[0]))}) as api:
            yield SimpleNamespace(now=now, insert=insert, uid=user_ids[0], stranger=user_ids[1],
                                  teams=team_ids, match_ids=match_ids, api=api, app=app)
    finally:
        await db.execute("DELETE FROM users WHERE id=ANY($1::uuid[])", user_ids)
        await db.execute("DELETE FROM matches WHERE id=ANY($1::uuid[])", match_ids)
        await db.execute("DELETE FROM teams WHERE id=ANY($1::uuid[])", team_ids)
        await db.disconnect()


@pytest.mark.parametrize("status", INACTIVE)
@pytest.mark.parametrize("freshness", ["kickoff", "state_update"])
async def test_exact_seven_day_boundary_for_every_inactive_status(dashboard, monkeypatch,
                                                                  status, freshness):
    d = dashboard
    rows = [await d.insert(status) for _ in range(3)]
    async with db.pool().acquire() as conn, conn.transaction():
        timestamp = "starts_at" if freshness == "kickoff" else "last_polled_at"
        for row, offset in zip(rows, [-1, 0, 1], strict=True):
            await conn.execute(
                f"UPDATE matches SET {timestamp}=now()-INTERVAL '168 hours'"
                "+$2*INTERVAL '1 microsecond' WHERE id=$1", row["id"], offset)
        # One transaction's PostgreSQL clock makes the exact boundary deterministic.
        monkeypatch.setattr(db, "fetch", conn.fetch)
        ids = {r["id"] for r in await matches.find_live_matches_for_user(d.uid)}
        assert rows[0]["id"] not in ids
        assert {r["id"] for r in rows[1:]} <= ids


@pytest.mark.parametrize("status", INACTIVE)
async def test_old_inactive_or_undated_checked_only_is_hidden(dashboard, status):
    d = dashboard
    old = await d.insert(status, d.now - timedelta(days=30),
                         d.now - timedelta(days=8), d.now)
    undated = await d.insert(status, checked=d.now)
    recent = await d.insert(status, d.now - timedelta(days=30), d.now)
    upcoming = await d.insert(status, d.now + timedelta(days=30))
    ids = {r["id"] for r in await matches.find_live_matches_for_user(d.uid)}
    assert old["id"] not in ids and undated["id"] not in ids
    assert recent["id"] in ids and upcoming["id"] in ids


async def test_live_preserved_even_old_or_undated_and_finished_rule_unchanged(dashboard):
    d = dashboard
    live_old = await d.insert("live", d.now - timedelta(days=30))
    live_undated = await d.insert("live")
    recent = await d.insert("finished", d.now - timedelta(days=30), d.now)
    fallback = await d.insert("finished", d.now - timedelta(hours=2))
    old = await d.insert("finished", d.now - timedelta(days=30),
                         d.now - timedelta(hours=25), d.now)
    ids = {r["id"] for r in await matches.find_live_matches_for_user(d.uid)}
    assert {live_old["id"], live_undated["id"], recent["id"], fallback["id"]} <= ids
    assert old["id"] not in ids


async def test_exact_finished_boundary_remains_twenty_four_hours(dashboard, monkeypatch):
    d = dashboard
    rows = [await d.insert("finished") for _ in range(3)]
    async with db.pool().acquire() as conn, conn.transaction():
        for row, offset in zip(rows, [-1, 0, 1], strict=True):
            await conn.execute(
                "UPDATE matches SET last_polled_at=now()-INTERVAL '24 hours'"
                "+$2*INTERVAL '1 microsecond' WHERE id=$1", row["id"], offset)
        monkeypatch.setattr(db, "fetch", conn.fetch)
        ids = {r["id"] for r in await matches.find_live_matches_for_user(d.uid)}
        assert rows[0]["id"] not in ids
        assert {r["id"] for r in rows[1:]} <= ids


@pytest.mark.parametrize("zone", ["UTC", "America/New_York", "Asia/Kolkata"])
@pytest.mark.parametrize("anchor", ["2026-03-08T12:00:00+00:00", "2026-11-01T12:00:00+00:00"])
@pytest.mark.parametrize("status,hours", [("finished", 24), ("scheduled", 168)])
@pytest.mark.parametrize("freshness", ["kickoff", "state_update"])
async def test_elapsed_cutoffs_are_timezone_independent_across_dst(
        dashboard, monkeypatch, zone, anchor, status, hours, freshness):
    d = dashboard
    rows = [await d.insert(status) for _ in range(3)]
    live = await d.insert("live")
    upcoming = await d.insert("scheduled", datetime.fromisoformat(anchor) + timedelta(days=30))
    timestamp = "starts_at" if freshness == "kickoff" else "last_polled_at"
    cutoff = datetime.fromisoformat(anchor) - timedelta(hours=hours)
    async with db.pool().acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('TimeZone',$1,true)", zone)
        for row, offset in zip(rows, [-1, 0, 1], strict=True):
            await conn.execute(f"UPDATE matches SET {timestamp}=$2 WHERE id=$1",
                               row["id"], cutoff + timedelta(microseconds=offset))

        async def fetch_at_anchor(query, *args):
            # Preserve the production predicate, substituting only its clock so
            # the test can exercise actual spring/fall DST dates deterministically.
            return await conn.fetch(query.replace("now()", "$2::timestamptz"),
                                    *args, datetime.fromisoformat(anchor))

        monkeypatch.setattr(db, "fetch", fetch_at_anchor)
        ids = {r["id"] for r in await matches.find_live_matches_for_user(d.uid)}
        assert rows[0]["id"] not in ids
        assert {r["id"] for r in rows[1:]} <= ids
        assert {live["id"], upcoming["id"]} <= ids


async def test_history_growth_does_not_grow_response_and_feeds_remain_bounded(dashboard):
    d = dashboard
    active = await d.insert("scheduled", d.now + timedelta(hours=1))
    hidden = await d.insert("cancelled", d.now - timedelta(days=30))
    await d.insert("live", d.now, followed=False)
    # Following both sides must not duplicate the fixture in the response.
    await subscriptions.create_subscription(d.uid, d.teams[1])
    await muted_matches.mute(d.uid, active["id"])
    await muted_matches.mute(d.uid, hidden["id"])
    for row in (active, hidden):
        for i in range(10):
            await match_events.record_event_if_new(row["id"], d.teams[0], "goal",
                                                   {"minute": i}, f"{row['id']}:goal:{i}")
    query = "SELECT * FROM match_events WHERE match_id=ANY($1::uuid[]) ORDER BY id"
    before_events = await db.pool().fetch(query, [active["id"], hidden["id"]])
    first = (await d.api.get("/api/matches/live")).json()
    assert len(first["matches"]) == 1
    assert first["matches"][0]["id"] == str(active["id"])
    assert first["matches"][0]["muted"] is True
    assert len(first["matches"][0]["events"]) == 6
    for _ in range(50):
        await d.insert("postponed", d.now - timedelta(days=100))
    assert (await d.api.get("/api/matches/live")).json() == first
    assert len(await matches.find_live_matches_for_user(d.stranger)) == 0
    detail = await d.api.get(f"/api/matches/{hidden['id']}")
    assert detail.status_code == 200
    assert detail.json()["match"]["muted"] is True
    assert len(detail.json()["events"]) == 10
    assert await matches.find_match_for_user(d.stranger, hidden["id"]) is None
    assert await db.pool().fetch(query, [active["id"], hidden["id"]]) == before_events
    assert await db.pool().fetchval("SELECT count(*) FROM matches WHERE id=ANY($1::uuid[])",
                                   d.match_ids) == len(d.match_ids)
    d.app.state.sports_client.get_team_fixtures.assert_not_awaited()
    d.app.state.sports_client.get_live_fixtures.assert_not_awaited()


async def test_ordering_and_concurrent_schedule_state_updates_preserve_active(dashboard):
    d = dashboard
    rows = [await d.insert("live", d.now) for _ in range(3)]
    upcoming = await d.insert("scheduled", d.now + timedelta(days=30))
    undated = await d.insert("live")
    target = rows[0]

    async def write():
        await matches.upsert_match(target["external_id"], d.teams[0], d.teams[1],
                                   "scheduled", 0, 0, d.now + timedelta(hours=1), None)
        await matches.update_match_state(target["id"], "live", 1, 0)

    async def read():
        for _ in range(5):
            result = await matches.find_live_matches_for_user(d.uid)
            assert {r["id"] for r in result} == {r["id"] for r in rows + [upcoming, undated]}
            assert [r["id"] for r in result] == [r["id"] for r in sorted(
                result, key=lambda r: (r["starts_at"] is None, r["starts_at"] or d.now, r["id"]))]

    await asyncio.gather(write(), read())
