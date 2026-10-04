"""Pure event-detection. Given the previous known state of a match and the
freshly-fetched state, return the events that occurred in between. No I/O and
no dedup ledger — that gating happens in the poller. Deterministic, so the
unit tests can pin exact output."""

from __future__ import annotations

from collections import Counter
from typing import Any


def diff_match(prev: dict[str, Any] | None, nxt: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    # First sight of a match (no prev): only lifecycle events implied by the
    # current status, never phantom goals for the score it's already at.
    before = prev or {"status": "scheduled", "home_score": 0, "away_score": 0}

    # Administrative/uncertain results must not manufacture goals or a
    # normal match completion. Retain them as state, without notifications.
    if nxt["status"] not in ("scheduled", "live", "finished"):
        return events

    # --- status transitions ---
    if (before.get("status") in ("scheduled", "postponed", "time_to_be_defined")
            and nxt["status"] == "live"):
        events.append(_lifecycle("kickoff", nxt))
    if before.get("status") != "finished" and nxt["status"] == "finished":
        events.append(_lifecycle("full_time", nxt))

    # Compare event identities, not array positions: providers reorder/correct lists.
    # Score increases gate goals so delayed historical details do not replay alerts.
    if prev is not None:
        new_goals = _new_events(prev.get("events", []), nxt.get("events", []), "Goal")
        for side in ("home", "away"):
            team = nxt[f"{side}_external_id"]
            old_score = before.get(f"{side}_score", 0)
            delta = max(0, nxt[f"{side}_score"] - old_score)
            candidates = [g for g in new_goals if g.get("team_external_id") == team]
            # On a Redis cache miss, the DB score is the baseline; use only the
            # newest scoring details instead of replaying the full event history.
            candidates = candidates[-delta:] if delta else []
            for index in range(delta):
                goal = _goal_from_score(team, nxt)
                goal[f"{side}_score"] = old_score + index + 1
                if index < len(candidates):
                    g = candidates[index]
                    goal.update(
                        minute=g.get("minute"), extra_minute=g.get("extra_minute"),
                        detail=g.get("detail") or "Goal", player=g.get("player"),
                        player_external_id=g.get("player_external_id"),
                        occurrence=g["occurrence"],
                    )
                events.append(goal)

        for card in _new_events(prev.get("events", []), nxt.get("events", []), "Card"):
            events.append({
                **card, "type": "card", "detail": card.get("detail") or "Card",
            })

    return events


def _event_identity(event: dict[str, Any]) -> tuple:
    return (
        event.get("type"), event.get("team_external_id"),
        event.get("player_external_id") or event.get("player"),
        event.get("minute"), event.get("extra_minute"), event.get("detail"),
    )


def _new_events(previous: list, current: list, event_type: str) -> list[dict]:
    before = Counter(_event_identity(e) for e in previous if e.get("type") == event_type)
    seen: Counter = Counter()
    result = []
    for event in current:
        if event.get("type") != event_type:
            continue
        identity = _event_identity(event)
        seen[identity] += 1
        if seen[identity] > before[identity]:
            result.append({**event, "occurrence": seen[identity]})
    return result


def _lifecycle(etype: str, nxt: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": etype,
        "team_external_id": None,  # lifecycle events fan out to both teams
        "minute": nxt.get("minute"),
        "detail": "Kick-off" if etype == "kickoff" else "Full-time",
        "home_score": nxt["home_score"],
        "away_score": nxt["away_score"],
    }


def _goal_from_score(team_external_id: str, nxt: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "goal",
        "team_external_id": team_external_id,
        "minute": nxt.get("minute"),
        "detail": "Goal",
        "home_score": nxt["home_score"],
        "away_score": nxt["away_score"],
    }
