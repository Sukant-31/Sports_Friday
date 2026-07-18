"""Fixture discovery. For every team at least one user follows, pull its
upcoming fixtures from the sports API and upsert them into `matches`, so the
poller has real fixtures to watch. Runs on a slow cadence (see poller.run) —
the free API tier has a tight daily request budget.
"""

from __future__ import annotations

from datetime import datetime

from app.config import settings
from app.logging_conf import get_logger
from app.repositories import matches as matches_repo
from app.repositories import teams as teams_repo
from app.sports_api import normalize
from app.sports_api.client import SportsApiError

log = get_logger("discovery")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def discover(client) -> int:
    """Returns the number of fixtures upserted."""
    teams = await teams_repo.find_subscribed_teams()
    if not teams:
        log.debug("no subscribed teams to discover fixtures for")
        return 0

    upserted = 0
    for team in teams:
        try:
            raw = await client.get_team_fixtures(
                team["external_id"], settings.fixtures_lookahead
            )
        except SportsApiError as exc:
            log.warning("fixture discovery failed for team %s: %s", team["external_id"], exc)
            continue

        for fx in normalize.normalize_fixtures(raw):
            home = await teams_repo.upsert_team(
                fx["home_external_id"], fx["home_team_name"] or fx["home_external_id"]
            )
            away = await teams_repo.upsert_team(
                fx["away_external_id"], fx["away_team_name"] or fx["away_external_id"]
            )
            await matches_repo.upsert_match(
                fx["external_id"],
                home["id"],
                away["id"],
                fx["status"],
                fx["home_score"],
                fx["away_score"],
                _parse_dt(fx.get("starts_at")),
                fx.get("minute"),
            )
            upserted += 1

    log.info("discovery upserted %d fixture(s) for %d team(s)", upserted, len(teams))
    return upserted
