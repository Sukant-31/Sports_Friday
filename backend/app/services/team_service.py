from __future__ import annotations

from fastapi import Request

from app.logging_conf import get_logger
from app.repositories import teams as teams_repo
from app.sports_api import normalize
from app.sports_api.client import SportsApiError

log = get_logger("team_service")


async def search_teams(request: Request, q: str) -> list[dict]:
    """Search-then-cache: serve from the local cache when it's rich enough,
    otherwise hit the sports API and upsert results into the teams table."""
    cached = await teams_repo.search_teams_cached(q)
    if len(cached) >= 5:
        return [dict(r) for r in cached]

    client = request.app.state.sports_client
    try:
        api_response = await client.search_teams(q)
        normalized = normalize.normalize_team_search(api_response)
        upserted = [
            await teams_repo.upsert_team(t["external_id"], t["name"], t["league"])
            for t in normalized
            if t["external_id"] and t["name"]
        ]
        merged = {r["id"]: dict(r) for r in [*cached, *upserted]}
        return list(merged.values())
    except SportsApiError as exc:
        log.warning("team search fell back to cache: %s", exc)
        return [dict(r) for r in cached]  # degrade gracefully
