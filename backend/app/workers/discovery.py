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
    await repair_team_names(client)
    teams = await teams_repo.find_subscribed_teams()
    if not teams:
        log.debug("no subscribed teams to discover fixtures for")
        return 0

    # A persistent client must not reuse yesterday's or a previous pass's feed.
    if hasattr(client, "begin_fixture_discovery"):
        client.begin_fixture_discovery()

    upserted = 0
    for team in teams:
        try:
            upserted += await discover_team(client, team["external_id"], refresh=False)
        except SportsApiError as exc:
            log.warning("fixture discovery failed for team %s: %s", team["external_id"], exc)

    log.info("discovery upserted %d fixture(s) for %d team(s)", upserted, len(teams))
    return upserted


async def repair_team_names(client) -> int:
    """Repair names previously replaced with provider IDs, without guessing."""
    repaired = 0
    for team in await teams_repo.find_teams_needing_name_repair():
        try:
            raw = await client.get_team(team["external_id"])
            for profile in normalize.normalize_team_search(raw):
                if (profile["external_id"] == team["external_id"]
                        and profile["name"] and profile["name"] != team["external_id"]):
                    await teams_repo.upsert_team(
                        profile["external_id"], profile["name"], profile["league"],
                    )
                    repaired += 1
        except SportsApiError as exc:
            log.warning("team name repair failed for %s: %s", team["external_id"], exc)
    return repaired


async def discover_team(client, external_id: str, *, refresh: bool = True) -> int:
    """Discover fixtures for one team, including immediately after a follow."""
    if refresh and hasattr(client, "begin_fixture_discovery"):
        client.begin_fixture_discovery()
    fixtures = {}
    failure = None
    # Try both endpoints even if one is unavailable on the provider's plan.
    for fetch in (
        lambda: client.get_team_live_fixtures(external_id),
        lambda: client.get_team_fixtures(external_id, settings.fixtures_lookahead),
    ):
        try:
            for fx in normalize.normalize_fixtures(await fetch()):
                fixtures.setdefault(fx["external_id"], fx)
        except SportsApiError as exc:
            failure = exc

    upserted = 0
    for fx in fixtures.values():
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
    if failure is not None:
        raise failure
    return upserted
