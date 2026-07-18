"""Pure event-detection. Given the previous known state of a match and the
freshly-fetched state, return the events that occurred in between. No I/O and
no dedup ledger — that gating happens in the poller. Deterministic, so the
unit tests can pin exact output."""

from __future__ import annotations

from typing import Any


def diff_match(prev: dict[str, Any] | None, nxt: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    # First sight of a match (no prev): only lifecycle events implied by the
    # current status, never phantom goals for the score it's already at.
    before = prev or {"status": "scheduled", "home_score": 0, "away_score": 0}

    # --- status transitions ---
    if before.get("status") != "live" and nxt["status"] == "live":
        events.append(_lifecycle("kickoff", nxt))
    if before.get("status") != "finished" and nxt["status"] == "finished":
        events.append(_lifecycle("full_time", nxt))

    # --- goals ---
    # Only with a baseline (prev) can we tell what's new; on first sight we emit
    # no goals, avoiding phantom alerts for the score the match is already at.
    if prev is not None:
        api_goals = [e for e in nxt.get("events", []) if e.get("type") == "Goal"]
        if api_goals:
            prev_goals = [e for e in prev.get("events", []) if e.get("type") == "Goal"]
            for g in api_goals[len(prev_goals):]:
                events.append(
                    {
                        "type": "goal",
                        "team_external_id": g.get("team_external_id"),
                        "minute": g.get("minute"),
                        "detail": g.get("detail") or "Goal",
                        "player": g.get("player"),
                        "player_external_id": g.get("player_external_id"),
                        "home_score": nxt["home_score"],
                        "away_score": nxt["away_score"],
                    }
                )
        else:
            if nxt["home_score"] > before.get("home_score", 0):
                events.append(_goal_from_score(nxt["home_external_id"], nxt))
            if nxt["away_score"] > before.get("away_score", 0):
                events.append(_goal_from_score(nxt["away_external_id"], nxt))

    # --- cards ---
    api_cards = [e for e in nxt.get("events", []) if e.get("type") == "Card"]
    if api_cards and prev is not None:
        prev_cards = [e for e in prev.get("events", []) if e.get("type") == "Card"]
        for c in api_cards[len(prev_cards):]:
            events.append(
                {
                    "type": "card",
                    "team_external_id": c.get("team_external_id"),
                    "minute": c.get("minute"),
                    "detail": c.get("detail") or "Card",
                    "player": c.get("player"),
                    "player_external_id": c.get("player_external_id"),
                }
            )

    return events


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
