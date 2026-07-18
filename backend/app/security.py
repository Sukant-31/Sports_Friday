"""Password hashing (bcrypt) and JWT issue/verify for the httpOnly auth cookie."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

_TOKEN_TTL = timedelta(days=7)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + _TOKEN_TTL}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> str:
    """Return the user id (sub) or raise jwt.PyJWTError."""
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    return payload["sub"]


def auth_cookie_kwargs() -> dict:
    return {
        "key": settings.auth_cookie_name,
        "httponly": True,
        "secure": settings.is_prod,
        "samesite": "lax",
        "max_age": int(_TOKEN_TTL.total_seconds()),
        "path": "/",
    }
