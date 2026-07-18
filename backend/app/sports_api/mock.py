"""Mock sports-API client + a scripted match timeline.

Lets the poller/notifier flow run end-to-end without a real API key or a live
game. The payloads are shaped exactly like API-Football responses so they pass
through the real `normalize` code path unchanged.
"""

from __future__ import annotations

from typing import Any

HOME_ID = 100
AWAY_ID = 200


def _fixture_obj(
    external_id: str,
    status_short: str,
    home_score: int,
    away_score: int,
    minute: int | None,
    events: list[dict[str, Any]],
    date: str | None = None,
) -> dict[str, Any]:
    """One raw API-Football-shaped fixture object."""
    return {
        "fixture": {
            "id": external_id,
            "date": date,
            "status": {"short": status_short, "elapsed": minute},
        },
        "teams": {
            "home": {"id": HOME_ID, "name": "Home FC"},
            "away": {"id": AWAY_ID, "name": "Away United"},
        },
        "goals": {"home": home_score, "away": away_score},
        "events": events,
    }


def _raw_fixture(
    external_id: str,
    status_short: str,
    home_score: int,
    away_score: int,
    minute: int | None,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """A single fixture wrapped in the `{response: [...]}` envelope."""
    return {"response": [_fixture_obj(external_id, status_short, home_score, away_score, minute, events)]}


def build_team_fixtures(match_external_id: str) -> dict[str, Any]:
    """A team-fixtures response (what discovery consumes): one upcoming match."""
    return {
        "response": [
            _fixture_obj(
                match_external_id, "NS", 0, 0, None, [], date="2026-08-01T14:00:00+00:00"
            )
        ]
    }


def _goal(team_id: int, minute: int, player_id: int, player: str) -> dict[str, Any]:
    return {
        "time": {"elapsed": minute},
        "team": {"id": team_id},
        "player": {"id": player_id, "name": player},
        "type": "Goal",
        "detail": "Normal Goal",
    }


def _card(team_id: int, minute: int, player_id: int, player: str, colour: str) -> dict[str, Any]:
    return {
        "time": {"elapsed": minute},
        "team": {"id": team_id},
        "player": {"id": player_id, "name": player},
        "type": "Card",
        "detail": f"{colour} Card",
    }


def build_timeline(match_external_id: str) -> list[tuple[str, dict[str, Any]]]:
    """A scripted match: kickoff, a goal, a card, another goal, a duplicate poll
    (should yield nothing), then full-time. Events are cumulative, as a real API
    returns them. All events are for the home team so the single subscribed user
    receives them."""
    g1 = _goal(HOME_ID, 23, 7, "A. Striker")
    c1 = _card(HOME_ID, 55, 9, "B. Midfielder", "Yellow")
    g2 = _goal(HOME_ID, 70, 11, "C. Winger")

    return [
        ("kickoff        1H 0-0", _raw_fixture(match_external_id, "1H", 0, 0, 3, [])),
        ("goal @23'      1H 1-0", _raw_fixture(match_external_id, "1H", 1, 0, 23, [g1])),
        ("card @55'      2H 1-0", _raw_fixture(match_external_id, "2H", 1, 0, 55, [g1, c1])),
        ("goal @70'      2H 2-0", _raw_fixture(match_external_id, "2H", 2, 0, 70, [g1, c1, g2])),
        ("re-poll (dup)  2H 2-0", _raw_fixture(match_external_id, "2H", 2, 0, 72, [g1, c1, g2])),
        ("full-time      FT 2-0", _raw_fixture(match_external_id, "FT", 2, 0, 90, [g1, c1, g2])),
    ]


class MockSportsApiClient:
    """Drop-in replacement for SportsApiClient over a scripted timeline. Returns
    the current snapshot; call advance() to move to the next one."""

    def __init__(self, timeline: list[tuple[str, dict[str, Any]]]) -> None:
        self._timeline = timeline
        self._idx = 0

    @property
    def label(self) -> str:
        return self._timeline[self._idx][0]

    def labels(self) -> list[str]:
        return [label for label, _ in self._timeline]

    def advance(self) -> bool:
        if self._idx < len(self._timeline) - 1:
            self._idx += 1
            return True
        return False

    async def get_live_fixture(self, external_id: str) -> dict[str, Any]:
        return self._timeline[self._idx][1]

    async def get_live_fixtures(self) -> dict[str, Any]:
        return self._timeline[self._idx][1]

    async def get_team_fixtures(self, team_external_id: str, count: int) -> dict[str, Any]:
        # Discovery over the same scripted match (as an upcoming fixture).
        match_external_id = self._timeline[0][1]["response"][0]["fixture"]["id"]
        return build_team_fixtures(match_external_id)

    async def search_teams(self, q: str) -> dict[str, Any]:  # pragma: no cover
        return {"response": []}

    async def aclose(self) -> None:
        return None
