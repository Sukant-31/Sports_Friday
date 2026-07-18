"""Redis client plus the short-lived per-match state cache the poller diffs
against. Redis is an optimisation; Postgres remains the source of truth."""

from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from app.config import settings

_STATE_TTL_SECONDS = 60 * 60 * 3  # matches don't run longer than ~3h

redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)


def _state_key(match_id: str) -> str:
    return f"match:{match_id}:state"


async def get_match_state(match_id: str) -> dict[str, Any] | None:
    raw = await redis.get(_state_key(match_id))
    return json.loads(raw) if raw else None


async def set_match_state(match_id: str, state: dict[str, Any]) -> None:
    await redis.set(_state_key(match_id), json.dumps(state), ex=_STATE_TTL_SECONDS)


async def close_redis() -> None:
    await redis.aclose()
