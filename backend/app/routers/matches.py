from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Depends

from app.deps import get_current_user_id
from app.repositories import matches as matches_repo

router = APIRouter(prefix="/api/matches", tags=["matches"])


@router.get("/live")
async def live(user_id: UUID = Depends(get_current_user_id)) -> dict:
    rows = await matches_repo.find_live_matches_for_user(user_id)
    matches = [dict(r) for r in rows]

    # Attach a recent-events feed to each match (goals/cards/kickoff/full-time).
    match_ids = [m["id"] for m in matches]
    events_by_match: dict = defaultdict(list)
    for ev in await matches_repo.find_recent_events_for_matches(match_ids):
        detail = ev["detail"] or {}
        events_by_match[ev["match_id"]].append(
            {
                "type": ev["type"],
                "minute": detail.get("minute"),
                "player": detail.get("player"),
                "detail": detail.get("detail"),
                "home_score": detail.get("home_score"),
                "away_score": detail.get("away_score"),
                "at": ev["created_at"],
            }
        )
    for m in matches:
        m["events"] = events_by_match.get(m["id"], [])

    return {"matches": matches}
