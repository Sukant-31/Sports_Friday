from app.workers.dedup_key import dedup_key


def test_stable_for_same_goal():
    goal = {"type": "goal", "team_external_id": "100", "home_score": 1, "away_score": 0}
    assert dedup_key("fx1", goal) == dedup_key("fx1", goal)


def test_distinct_across_teams():
    home = {"type": "goal", "team_external_id": "100", "home_score": 1, "away_score": 0}
    away = {"type": "goal", "team_external_id": "200", "home_score": 1, "away_score": 1}
    assert dedup_key("fx1", home) != dedup_key("fx1", away)


def test_scorer_identity_preferred():
    g1 = {
        "type": "goal",
        "team_external_id": "100",
        "player_external_id": "7",
        "minute": 12,
    }
    g2 = {
        "type": "goal",
        "team_external_id": "100",
        "player_external_id": "9",
        "minute": 12,
    }
    assert dedup_key("fx1", g1) != dedup_key("fx1", g2)


def test_lifecycle_keys_are_singletons():
    assert dedup_key("fx1", {"type": "kickoff"}) == "fx1:kickoff"
    assert dedup_key("fx1", {"type": "full_time"}) == "fx1:full_time"
