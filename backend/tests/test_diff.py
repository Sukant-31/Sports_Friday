"""Pure unit tests for the core event-detection logic. No DB, no live match."""

from app.workers.diff import diff_match

BASE = {
    "status": "live",
    "minute": 10,
    "home_external_id": "100",
    "away_external_id": "200",
    "home_score": 0,
    "away_score": 0,
    "events": [],
}


def test_no_change_yields_no_events():
    assert diff_match(BASE, {**BASE}) == []


def test_home_score_increment_yields_one_home_goal():
    events = diff_match(BASE, {**BASE, "home_score": 1, "minute": 23})
    assert len(events) == 1
    assert events[0]["type"] == "goal"
    assert events[0]["team_external_id"] == "100"
    assert events[0]["home_score"] == 1


def test_kickoff_on_scheduled_to_live():
    prev = {**BASE, "status": "scheduled"}
    events = diff_match(prev, {**BASE, "status": "live"})
    assert [e["type"] for e in events] == ["kickoff"]


def test_full_time_on_live_to_finished():
    events = diff_match(BASE, {**BASE, "status": "finished"})
    assert [e["type"] for e in events] == ["full_time"]


def test_first_sight_emits_no_phantom_goals():
    events = diff_match(None, {**BASE, "home_score": 2, "away_score": 1})
    # status already 'live' vs default 'scheduled' -> kickoff only, no goals
    assert [e["type"] for e in events] == ["kickoff"]


def test_reprocessing_same_snapshot_is_idempotent():
    nxt = {**BASE, "home_score": 1}
    first = diff_match(BASE, nxt)
    second = diff_match(nxt, nxt)
    assert len(first) == 1
    assert second == []
