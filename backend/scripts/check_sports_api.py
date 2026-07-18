"""Diagnostic: verify a real SPORTS_API_KEY works and inspect the shapes.

Hits the live sports API (no DB needed) and prints a sanity report — whether
the key authenticates, how many live fixtures are returned, and a normalized
sample. Run once after setting your key:

    SPORTS_API_KEY=xxxx python scripts/check_sports_api.py
    # or put it in ../.env and: set -a; source ../.env; set +a; python scripts/check_sports_api.py
"""

from __future__ import annotations

import asyncio

from app.config import settings
from app.sports_api import normalize
from app.sports_api.client import SportsApiClient, SportsApiError


async def main() -> None:
    print(f"provider : {settings.sports_api_provider}")
    print(f"base_url : {settings.sports_api_base_url}")
    print(f"key set  : {'yes' if settings.sports_api_key else 'NO — set SPORTS_API_KEY'}")
    if not settings.sports_api_key:
        return

    client = SportsApiClient()
    try:
        # 1. Live fixtures right now.
        raw = await client.get_live_fixtures()
        errors = raw.get("errors")
        if errors:
            print(f"\nAPI returned errors: {errors}")
            print("(A plan/subscription or wrong header is the usual cause.)")
            return

        results = raw.get("results", len(raw.get("response", [])))
        print(f"\nlive fixtures now: {results}")

        fixtures = normalize.normalize_fixtures(raw)
        for fx in fixtures[:5]:
            print(
                f"  {fx['home_team_name']} {fx['home_score']}-{fx['away_score']} "
                f"{fx['away_team_name']}  [{fx['status']} {fx['minute'] or ''}']"
            )

        # 2. Upcoming fixtures for one team (exercises the discovery endpoint).
        #    43 = Arsenal on API-Football; change as you like.
        upcoming = await client.get_team_fixtures("43", 3)
        up = normalize.normalize_fixtures(upcoming)
        print(f"\nupcoming for team 43: {len(up)} fixture(s)")
        for fx in up[:3]:
            print(
                f"  {fx['starts_at']}  {fx['home_team_name']} vs {fx['away_team_name']} "
                f"[{fx['status']}]"
            )
        print("\nOK — the key works and responses normalize cleanly.")
    except SportsApiError as exc:
        print(f"\nrequest failed: {exc} (status={exc.status_code})")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
