"""FastAPI dependencies."""

from __future__ import annotations

from uuid import UUID

import jwt
from fastapi import Cookie, HTTPException, status

from app.config import settings
from app.security import decode_token


def get_current_user_id(
    token: str | None = Cookie(default=None, alias=settings.auth_cookie_name),
) -> UUID:
    """Extract and validate the user id (as a UUID) from the auth cookie.

    Returning a UUID (not str) keeps asyncpg happy — it binds uuid columns
    natively without needing ::uuid casts in every query.
    """
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        return UUID(decode_token(token))
    except (jwt.PyJWTError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid or expired session"
        ) from exc
