"""Set up a live test: ensure a demo user, follow one or more teams (resolved
against the real sports API), register a console push subscription, and run an
immediate fixture-discovery pass so the poller has matches right away.

    python scripts/live_setup.py "Arsenal" "Real Madrid"

Requires a working SPORTS_API_KEY. Prints login credentials for the frontend
and lists each followed team's upcoming/live fixtures so you know whether there
is anything to watch right now.
"""

from __future__ import annotations

import asyncio
import sys

from app import db
from app.config import settings
from app.repositories import push_subscriptions as push_repo
from app.repositories import subscriptions as subs_repo
from app.repositories import teams as teams_repo
from app.repositories import users as users_repo
from app.security import hash_password
from app.sports_api import normalize
from app.sports_api.client import SportsApiClient, SportsApiError
from app.workers.discovery import discover

DEMO_EMAIL = "demo@local"
DEMO_PASSWORD = "demo1234"


async def ensure_user() -> dict:
    existing = await users_repo.find_user_by_email(DEMO_EMAIL)
    if existing:
        return existing
    return await users_repo.create_user(DEMO_EMAIL, hash_password(DEMO_PASSWORD))


async def follow_team(client: SportsApiClient, user_id, name: str) -> dict | None:
    resp = await client.search_teams(name)
    results = normalize.normalize_team_search(resp)
    if not results:
        print(f"  ✗ no team found for '{name}'")
        return None
    top = results[0]
    team = await teams_repo.upsert_team(top["external_id"], top["name"], top.get("league"))
    await subs_repo.create_subscription(user_id, team["id"], True, True, True)
    print(f"  ✓ following {team['name']} (external id {team['external_id']})")
    return team


async def show_fixtures(client: SportsApiClient, team: dict) -> None:
    try:
        resp = await client.get_team_fixtures(team["external_id"], 3)
    except SportsApiError as exc:
        print(f"    (couldn't fetch fixtures: {exc})")
        return
    fixtures = normalize.normalize_fixtures(resp)
    if not fixtures:
        print("    no upcoming fixtures returned")
        return
    for fx in fixtures:
        live = " ← LIVE NOW" if fx["status"] == "live" else ""
        print(
            f"    {fx.get('starts_at') or 'TBD'}  {fx['home_team_name']} vs "
            f"{fx['away_team_name']}  [{fx['status']}]{live}"
        )


async def main() -> None:
    team_names = sys.argv[1:] or ["Arsenal"]

    if not settings.sports_api_key:
        print("SPORTS_API_KEY is not set — the live test needs a real key.")
        sys.exit(1)

    await db.connect()
    client = SportsApiClient()
    try:
        user = await ensure_user()
        # Console push transport ignores the endpoint, but a row must exist for
        # the notifier to resolve a target.
        await push_repo.upsert_push_subscription(
            user["id"], "console://demo", "p256dh-console", "auth-console"
        )

        print(f"\ndemo user: {DEMO_EMAIL} / {DEMO_PASSWORD}\n")
        print("following teams:")
        teams = []
        for name in team_names:
            team = await follow_team(client, user["id"], name)
            if team:
                teams.append(team)

        print("\nupcoming/live fixtures for followed teams:")
        for team in teams:
            print(f"  {team['name']}:")
            await show_fixtures(client, team)

        print("\nrunning an immediate discovery pass…")
        n = await discover(client)
        print(f"discovery upserted {n} fixture(s) into `matches`.")
        print("\nready — start the services (or use scripts/live_test.sh).")
    finally:
        await client.aclose()
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
