"""Fixture discovery. For every team at least one user follows, pull its
upcoming fixtures from the sports API and upsert them into `matches`, so the
poller has real fixtures to watch. Runs on a slow cadence (see poller.run) —
the free API tier has a tight daily request budget.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import settings
from app.logging_conf import get_logger
from app.repositories import matches as matches_repo
from app.repositories import teams as teams_repo
from app.sports_api import normalize
from app.sports_api.client import ApiRequestSkipped, PartialFixtureDiscovery, SportsApiError
from app.workers.run_report import RunReport

log = get_logger("discovery")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def discover(client, *, report: RunReport | None = None) -> int:
    """Returns the number of fixtures upserted."""
    teams = await teams_repo.find_subscribed_teams()
    if report is not None:
        report.followed_teams = len(teams)
    if not teams:
        log.debug("no subscribed teams to discover fixtures for")
        return 0

    # A persistent client must not reuse yesterday's or a previous pass's feed.
    if hasattr(client, "begin_fixture_discovery"):
        client.begin_fixture_discovery()

    upserted = 0
    seen = set()
    for team in teams:
        refreshed = team.get('last_discovered_at')
        if refreshed and datetime.now(timezone.utc) - refreshed < timedelta(
                seconds=settings.fixture_discovery_refresh_seconds):
            continue
        try:
            upserted += await discover_team(client, team["external_id"], refresh=False,
                                           report=report, seen=seen)
            if report is not None:
                report.record('discovery', team['external_id'])
        except ApiRequestSkipped:
            continue
        except SportsApiError as exc:
            if report is not None:
                report.record('discovery', team['external_id'], exc)
            log.warning("fixture discovery failed for team %s: %s", team["external_id"], exc)

    await repair_team_names(client, report=report)
    log.info("discovery upserted %d fixture(s) for %d team(s)", upserted, len(teams))
    return upserted


async def repair_team_names(client, *, report: RunReport | None = None) -> int:
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
            if report is not None:
                report.record('name_repair', team['external_id'])
        except ApiRequestSkipped:
            break
        except SportsApiError as exc:
            if report is not None:
                report.record('name_repair', team['external_id'], exc)
            log.warning("team name repair failed for %s: %s", team["external_id"], exc)
    return repaired


async def discover_team(client, external_id: str, *, refresh: bool = True,
                        report: RunReport | None = None, seen: set | None = None) -> int:
    """Discover fixtures for one team, including immediately after a follow."""
    if refresh and hasattr(client, "begin_fixture_discovery"):
        client.begin_fixture_discovery()
    failure = None
    try:
        raw = await client.get_team_fixtures(external_id, settings.fixtures_lookahead)
    except PartialFixtureDiscovery as exc:
        failure = exc
        raw = exc.fixtures
    fixtures = normalize.normalize_fixtures(raw)

    upserted = 0
    for fx in fixtures:
        if seen is not None and fx['external_id'] in seen:
            continue
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
        if seen is not None:
            seen.add(fx['external_id'])
        if report is not None:
            report.fixtures_upserted += 1
    if failure is not None:
        if isinstance(failure.error, ApiRequestSkipped):
            raise failure.error
        raise failure
    await teams_repo.mark_discovered(external_id)
    return upserted
