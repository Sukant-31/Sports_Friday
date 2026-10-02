from __future__ import annotations

from fastapi import Request

from app.logging_conf import get_logger
from app.repositories import teams as teams_repo
from app.services.team_search import LIMIT, normalize_query, rank_teams
from app.sports_api import normalize
from app.sports_api.client import SportsApiError

log = get_logger("team_service")


async def search_teams(request: Request, q: str) -> dict:
    """Search-then-cache: serve from the local cache when it's rich enough,
    otherwise hit the sports API and upsert results into the teams table."""
    q = normalize_query(q)
    if len(q) < 3:
        return {"teams": [], "warning": None}
    cached = await teams_repo.search_teams_cached(q)
    ranked_cached = rank_teams(cached, q)
    if len(ranked_cached) >= LIMIT:
        return {"teams": ranked_cached, "warning": None}

    client = request.app.state.sports_client
    try:
        # Provider searches can be literal; a broad token also discovers abbreviated names.
        provider_query = max(q.split(), key=len) if len(q.split()) > 1 else q
        if len(provider_query) < 3:
            return {"teams": ranked_cached, "warning": None}
        api_response = await client.search_teams(provider_query)
        normalized = normalize.normalize_team_search(api_response)
        upserted = [
            await teams_repo.upsert_team(t["external_id"], t["name"], t["league"])
            for t in normalized
            if t["external_id"] and t["name"]
        ]
        merged = {r["id"]: dict(r) for r in [*cached, *upserted]}
        return {"teams": rank_teams(merged.values(), q), "warning": None}
    except SportsApiError as exc:
        log.warning("team search fell back to cache: %s", exc)
        return {"teams": ranked_cached,
                "warning": "Live team search is unavailable. Showing saved results; try again later."}
