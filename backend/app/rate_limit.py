"""Shared, fail-closed Redis limits for explicitly protected HTTP routes."""

import math
import os
import time
from ipaddress import ip_address
from urllib.parse import urlsplit
from uuid import UUID

import jwt
from fastapi.responses import JSONResponse
from limits.errors import StorageError
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.security import decode_token


class RedisLimiter(Limiter):
    def get_app_config(self, key, default=None):
        # Settings below are authoritative: SlowAPI's implicit RATELIMIT_*
        # environment options must not enable memory fallback, swallow storage
        # errors, or add global limits to logout/subscription cleanup.
        return default


def request_identity(request) -> str:
    # Credentials endpoints always use the peer, even if a cookie is supplied.
    # Caller-controlled forwarding headers are never identity authorities.
    if request.url.path in {"/api/teams/search", "/api/push/test", "/api/push/subscribe"}:
        token = request.cookies.get(settings.auth_cookie_name)
        if token:
            try:
                return f"user:{UUID(decode_token(token))}"
            except (jwt.PyJWTError, ValueError, KeyError):
                pass
    # Only Vercel's runtime may trust its edge-overwritten client-IP header.
    # Locally/behind other proxies, ignore every forwarding header. Never take
    # the first entry of an arbitrary caller-supplied forwarding chain.
    if os.environ.get("VERCEL") == "1":
        value = request.headers.get("x-vercel-forwarded-for", "")
        try:
            return f"peer:{ip_address(value)}"
        except ValueError:
            pass
    return f"peer:{get_remote_address(request)}"


def create_limiter(environment: str, *, storage_uri: str | None = None) -> Limiter:
    deployment = os.environ.get("VERCEL_ENV", "local")
    uri = storage_uri or settings.redis_url
    if urlsplit(uri).scheme not in {"redis", "rediss", "redis+unix"}:
        raise ValueError("HTTP rate limiting requires Redis storage")
    return RedisLimiter(
        key_func=request_identity,
        storage_uri=uri,
        key_prefix=f"sports-friday:http-rate-limit:{environment}:{deployment}",
        strategy="fixed-window",
        storage_options={"socket_connect_timeout": 1, "socket_timeout": 1, "wrap_exceptions": True},
        swallow_errors=False,
        in_memory_fallback_enabled=False,
        headers_enabled=False,
    )


def storage_unavailable(request, exc):
    return JSONResponse(
        status_code=503,
        content={"error": "Rate-limit storage unavailable, try again later"},
        headers={"Retry-After": "5"},
    )


def rate_limit_exceeded(request, exc):
    item, identifiers = request.state.view_rate_limit
    try:
        reset, _remaining = request.app.state.limiter.limiter.get_window_stats(item, *identifiers)
    except StorageError as error:
        return storage_unavailable(request, error)
    return JSONResponse(
        status_code=429,
        content={"error": "Too many requests, try again later"},
        headers={"Retry-After": str(max(1, math.ceil(reset - time.time())))},
    )


limiter = create_limiter(settings.env)
