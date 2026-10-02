"""Pure unit tests for the core event-detection logic. No DB, no live match."""

from app.workers.dedup_key import dedup_key
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


def goal(minute, player="7", **kwargs):
    return {"type": "Goal", "team_external_id": "100", "minute": minute,
            "player_external_id": player, "detail": "Normal Goal", **kwargs}


def test_multiple_goals_without_details_have_distinct_keys():
    events = diff_match(BASE, {**BASE, "home_score": 3})
    assert len(events) == 3
    assert [e["home_score"] for e in events] == [1, 2, 3]
    assert len({dedup_key("fixture", e) for e in events}) == 3


def test_reordering_does_not_replay_goals():
    previous = {**BASE, "home_score": 2, "events": [goal(10), goal(20)]}
    assert diff_match(previous, {**previous, "events": [goal(20), goal(10)]}) == []


def test_replaced_event_with_same_count_and_score_increase_is_detected():
    previous = {**BASE, "home_score": 1, "events": [goal(10)]}
    events = diff_match(previous, {**BASE, "home_score": 2, "events": [goal(20, "8")]})
    assert len(events) == 1 and events[0]["player_external_id"] == "8"


def test_delayed_goal_details_do_not_duplicate_score_alert():
    previous = {**BASE, "home_score": 1}
    assert diff_match(previous, {**previous, "events": [goal(10)]}) == []


def test_score_correction_does_not_create_goal():
    previous = {**BASE, "home_score": 2, "events": [goal(10), goal(20)]}
    assert diff_match(previous, {**BASE, "home_score": 1, "events": [goal(10)]}) == []


def test_cache_miss_uses_score_to_skip_historical_details():
    events = diff_match({**BASE, "home_score": 1},
                        {**BASE, "home_score": 2, "events": [goal(10), goal(20, "8")]})
    assert len(events) == 1 and events[0]["player_external_id"] == "8"


def test_identical_scorer_and_minute_occurrences_are_distinct():
    events = diff_match(BASE, {**BASE, "home_score": 2, "events": [goal(10), goal(10)]})
    assert len({dedup_key("fixture", e) for e in events}) == 2


def test_stoppage_time_goals_are_distinct():
    events = diff_match(BASE, {**BASE, "home_score": 2,
                              "events": [goal(90, extra_minute=1), goal(90, extra_minute=3)]})
    assert len({dedup_key("fixture", e) for e in events}) == 2


def test_same_player_can_receive_same_card_in_different_minutes():
    first = {**goal(10), "type": "Card", "detail": "Yellow Card"}
    second = {**first, "minute": 20}
    events = diff_match({**BASE, "events": [first]}, {**BASE, "events": [second, first]})
    assert len(events) == 1 and events[0]["minute"] == 20
    assert dedup_key("fixture", {**first, "type": "card"}) != dedup_key("fixture", events[0])
