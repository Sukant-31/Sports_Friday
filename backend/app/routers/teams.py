from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.deps import get_current_user_id
from app.rate_limit import limiter
from app.schemas import TeamOut
from app.services import team_service

router = APIRouter(prefix="/api/teams", tags=["teams"])


@router.get("/search")
@limiter.limit("30/minute")
async def search(
    request: Request,
    q: str = Query(min_length=2, max_length=60),
    _user: UUID = Depends(get_current_user_id),
) -> dict:
    teams = await team_service.search_teams(request, q)
    return {"teams": [TeamOut(**t) for t in teams]}
