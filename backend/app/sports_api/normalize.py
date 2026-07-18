"""Translate raw sports-API payloads into the internal shapes the rest of the
app depends on. A provider swap touches only this file (and client.py)."""

from __future__ import annotations

from typing import Any

_LIVE_CODES = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE"}
_FINISHED_CODES = {"FT", "AET", "PEN"}


def normalize_status(short_code: str | None) -> str:
    if short_code in _FINISHED_CODES:
        return "finished"
    if short_code in _LIVE_CODES:
        return "live"
    return "scheduled"


def normalize_team(raw: dict[str, Any]) -> dict[str, Any]:
    team = raw.get("team", raw)
    return {
        "external_id": str(team.get("id")),
        "name": team.get("name"),
        "league": (raw.get("league") or {}).get("name"),
    }


def normalize_team_search(api_response: dict[str, Any]) -> list[dict[str, Any]]:
    return [normalize_team(item) for item in api_response.get("response", [])]


def normalize_fixture(raw: dict[str, Any]) -> dict[str, Any]:
    fixture = raw["fixture"]
    teams = raw["teams"]
    goals = raw["goals"]
    status = fixture.get("status") or {}
    return {
        "external_id": str(fixture["id"]),
        "status": normalize_status(status.get("short")),
        "minute": status.get("elapsed"),
        "starts_at": fixture.get("date"),  # ISO 8601 string, or None
        "home_external_id": str(teams["home"]["id"]),
        "away_external_id": str(teams["away"]["id"]),
        "home_team_name": teams["home"].get("name"),
        "away_team_name": teams["away"].get("name"),
        "home_score": goals.get("home") or 0,
        "away_score": goals.get("away") or 0,
        "events": [
            {
                "type": e.get("type"),  # 'Goal' | 'Card' | ...
                "detail": e.get("detail"),
                "minute": (e.get("time") or {}).get("elapsed"),
                "team_external_id": str((e.get("team") or {}).get("id")),
                "player": (e.get("player") or {}).get("name"),
                "player_external_id": (
                    str((e.get("player") or {}).get("id"))
                    if (e.get("player") or {}).get("id") is not None
                    else None
                ),
            }
            for e in raw.get("events", [])
        ],
    }


def normalize_fixtures(api_response: dict[str, Any]) -> list[dict[str, Any]]:
    return [normalize_fixture(item) for item in api_response.get("response", [])]
