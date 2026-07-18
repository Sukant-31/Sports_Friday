from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from app.deps import get_current_user_id
from app.repositories import matches as matches_repo

router = APIRouter(prefix="/api/matches", tags=["matches"])


@router.get("/live")
async def live(user_id: UUID = Depends(get_current_user_id)) -> dict:
    rows = await matches_repo.find_live_matches_for_user(user_id)
    return {"matches": [dict(r) for r in rows]}
