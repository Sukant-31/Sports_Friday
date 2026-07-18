"""asyncpg connection pool. Raw SQL keeps parity with the plain-SQL migrations
and the 'no heavy ORM' design intent from the architecture doc."""

from __future__ import annotations

import json
import ssl
from typing import Any

import asyncpg

from app.config import settings

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    # Encode/decode jsonb as Python dict/list transparently, so callers pass and
    # receive dicts (not JSON strings) for `detail` columns.
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def connect() -> asyncpg.Pool:
    """Create the pool. Call once on app/worker startup."""
    global _pool
    if _pool is None:
        ssl_ctx = ssl.create_default_context() if settings.is_prod else None
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=1,
            max_size=10,
            ssl=ssl_ctx,
            init=_init_connection,
        )
    return _pool


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call connect() first")
    return _pool


async def fetch(query: str, *args: Any) -> list[asyncpg.Record]:
    return await pool().fetch(query, *args)


async def fetchrow(query: str, *args: Any) -> asyncpg.Record | None:
    return await pool().fetchrow(query, *args)


async def execute(query: str, *args: Any) -> str:
    return await pool().execute(query, *args)
