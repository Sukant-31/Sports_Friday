from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.deps import get_current_user_id
from app.rate_limit import limiter
from app.schemas import TeamOut
from app.services import team_service

router = APIRouter(prefix="/api/teams", tags=["teams"])


@router.get("/search")
@limiter.limit("120/minute")
async def search(
    request: Request,
    _user: Annotated[UUID, Depends(get_current_user_id)],
    q: str = Query(default="", max_length=60),
) -> dict:
    result = await team_service.search_teams(request, q)
    return {"teams": [TeamOut(**t) for t in result["teams"]], "warning": result["warning"]}
