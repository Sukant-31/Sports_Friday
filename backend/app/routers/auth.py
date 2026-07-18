from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.config import settings
from app.rate_limit import limiter
from app.schemas import Credentials, UserOut
from app.security import auth_cookie_kwargs, create_token
from app.services import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_auth_cookie(response: Response, user_id) -> None:
    response.set_cookie(value=create_token(user_id), **auth_cookie_kwargs())


@router.post("/signup", status_code=status.HTTP_201_CREATED)
@limiter.limit("20/15minutes")
async def signup(request: Request, body: Credentials, response: Response) -> dict:
    user = await auth_service.signup(body.email, body.password)
    _set_auth_cookie(response, user["id"])
    return {"user": UserOut(**user)}


@router.post("/login")
@limiter.limit("20/15minutes")
async def login(request: Request, body: Credentials, response: Response) -> dict:
    user = await auth_service.login(body.email, body.password)
    _set_auth_cookie(response, user["id"])
    return {"user": UserOut(**user)}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(settings.auth_cookie_name, path="/")
    return {"ok": True}
