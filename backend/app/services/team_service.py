from __future__ import annotations

import asyncio

from fastapi import Request

from app.logging_conf import get_logger
from app.repositories import teams as teams_repo
from app.services.team_search import normalize_query, rank_teams
from app.sports_api import normalize
from app.sports_api.client import SportsApiError

log = get_logger("team_service")

_PROVIDER_SEARCH_TIMEOUT_SECONDS = 2.0
_LIVE_SEARCH_WARNING = "Live team search is unavailable. Showing saved results; try again later."


async def search_teams(request: Request, q: str) -> dict:
    """Search-then-cache: serve from the local cache when it's rich enough,
    otherwise hit the sports API and upsert results into the teams table."""
    q = normalize_query(q)
    if len(q) < 3:
        return {"teams": [], "warning": None}
    cached = await teams_repo.search_teams_cached(q)
    ranked_cached = rank_teams(cached, q)
    # A useful local match is enough for an interactive response. Waiting for
    # the provider merely to fill all 15 display slots makes query-specific
    # provider cache misses block otherwise complete searches.
    if ranked_cached:
        return {"teams": ranked_cached, "warning": None}

    client = request.app.state.sports_client
    try:
        # Provider searches can be literal; a broad token also discovers abbreviated names.
        provider_query = max(q.split(), key=len) if len(q.split()) > 1 else q
        if len(provider_query) < 3:
            return {"teams": ranked_cached, "warning": None}
        # Team search is interactive and the provider is only a fallback. Do
        # not make an empty/local result wait for the client's much longer
        # general-purpose HTTP timeout when API-Football is unavailable.
        api_response = await asyncio.wait_for(
            client.search_teams(provider_query),
            timeout=_PROVIDER_SEARCH_TIMEOUT_SECONDS,
        )
        normalized = normalize.normalize_team_search(api_response)
        upserted = [
            await teams_repo.upsert_team(t["external_id"], t["name"], t["league"])
            for t in normalized
            if t["external_id"] and t["name"]
        ]
        merged = {r["id"]: dict(r) for r in [*cached, *upserted]}
        return {"teams": rank_teams(merged.values(), q), "warning": None}
    except TimeoutError:
        log.warning("team search provider timed out after %.1fs", _PROVIDER_SEARCH_TIMEOUT_SECONDS)
        return {"teams": ranked_cached, "warning": _LIVE_SEARCH_WARNING}
    except SportsApiError as exc:
        log.warning("team search fell back to cache: %s", exc)
        return {"teams": ranked_cached, "warning": _LIVE_SEARCH_WARNING}
