"""Deterministic identity for an event, so the same real-world occurrence
always maps to the same key. Pure function — no I/O — trivially unit-testable."""

from __future__ import annotations

from typing import Any


def dedup_key(external_match_id: str, event: dict[str, Any]) -> str:
    etype = event["type"]
    if etype == "goal":
        # Prefer scorer identity when available (handles two goals in one minute,
        # own goals, etc.); else fall back to the resulting aggregate score.
        if event.get("player_external_id"):
            return (
                f"{external_match_id}:goal:{event.get('team_external_id')}:"
                f"{event['player_external_id']}:{event.get('minute', 'na')}"
            )
        return f"{external_match_id}:goal:{event.get('home_score')}-{event.get('away_score')}"
    if etype == "card":
        who = event.get("player_external_id") or event.get("minute")
        return f"{external_match_id}:card:{event.get('team_external_id')}:{who}:{event.get('detail', '')}"
    if etype == "kickoff":
        return f"{external_match_id}:kickoff"
    if etype == "full_time":
        return f"{external_match_id}:full_time"
    return f"{external_match_id}:{etype}:{event.get('minute', 'na')}"
