"""arq queue wiring. The poller enqueues 'notify_match_event' jobs; the arq
worker (app/workers/notifier.py) consumes them. Replaces BullMQ."""

from __future__ import annotations

from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import settings


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


async def get_queue() -> ArqRedis:
    return await create_pool(redis_settings())


async def enqueue_match_event(queue: ArqRedis, payload: dict[str, Any]) -> None:
    # Retries/backoff are configured on the worker function (see notifier.py).
    await queue.enqueue_job("notify_match_event", payload)
