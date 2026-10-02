from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.config import settings
from app.deps import get_current_user_id
from app.repositories import matches as matches_repo
from app.repositories import muted_matches as muted_repo

router = APIRouter(prefix="/api/matches", tags=["matches"])


def _data_warning(matches) -> str | None:
    if not settings.sports_api_key:
        return "Live sports data is not configured. Scores cannot update until the service is configured."
    now = datetime.now(UTC)
    for match in matches:
        if match["status"] != "live":
            continue
        polled = match.get("last_polled_at")
        if polled is None or now - polled > timedelta(seconds=settings.live_poll_interval_seconds + 120):
            return "Live scores may be out of date. Updates are delayed; showing the last saved data."
    return None


def _shape_event(ev) -> dict:
    detail = ev["detail"] or {}
    return {
        "type": ev["type"],
        "minute": detail.get("minute"),
        "player": detail.get("player"),
        "detail": detail.get("detail"),
        "home_score": detail.get("home_score"),
        "away_score": detail.get("away_score"),
        "at": ev["created_at"],
    }


@router.get("/live")
async def live(user_id: Annotated[UUID, Depends(get_current_user_id)]) -> dict:
    rows = await matches_repo.find_live_matches_for_user(user_id)
    matches = [dict(r) for r in rows]

    # Attach a recent-events feed and the mute flag to each match.
    match_ids = [m["id"] for m in matches]
    events_by_match: dict = defaultdict(list)
    for ev in await matches_repo.find_recent_events_for_matches(match_ids):
        events_by_match[ev["match_id"]].append(_shape_event(ev))
    muted = await muted_repo.list_muted_match_ids(user_id)
    for m in matches:
        m["events"] = events_by_match.get(m["id"], [])
        m["muted"] = m["id"] in muted

    return {"matches": matches, "warning": _data_warning(matches)}


@router.get("/{match_id}")
async def detail(match_id: UUID, user_id: Annotated[UUID, Depends(get_current_user_id)]) -> dict:
    match = await matches_repo.find_match_for_user(user_id, match_id)
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Match not found")
    events = await matches_repo.find_all_events_for_match(match_id)
    out = dict(match)
    out["muted"] = await muted_repo.is_muted(user_id, match_id)
    return {"match": out, "events": [_shape_event(ev) for ev in events],
            "warning": _data_warning([out])}


async def _require_visible_match(user_id, match_id) -> None:
    if await matches_repo.find_match_for_user(user_id, match_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Match not found")


@router.post("/{match_id}/mute")
async def mute(match_id: UUID, user_id: Annotated[UUID, Depends(get_current_user_id)]) -> dict:
    await _require_visible_match(user_id, match_id)
    await muted_repo.mute(user_id, match_id)
    return {"muted": True}


@router.delete("/{match_id}/mute", status_code=status.HTTP_204_NO_CONTENT)
async def unmute(match_id: UUID, user_id: Annotated[UUID, Depends(get_current_user_id)]) -> Response:
    await muted_repo.unmute(user_id, match_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
