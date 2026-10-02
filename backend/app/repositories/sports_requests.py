"""PostgreSQL coordinates API budgets/cache across workers and Vercel instances."""
from contextlib import asynccontextmanager

from app import db


async def _used(conn, scope: str) -> int:
    return await conn.fetchval("SELECT COALESCE(sum(units),0)::int FROM sports_api_requests "
                               "WHERE scope=$1 AND requested_at > clock_timestamp()-INTERVAL '24 hours'",
                               scope)


async def reserve(scope: str, limit: int) -> bool:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(hashtext($1))', 'budget:' + scope)
            if await _used(conn, scope) >= limit:
                return False
            await conn.execute('INSERT INTO sports_api_requests(scope) VALUES ($1)', scope)
            await conn.execute("DELETE FROM sports_api_requests WHERE scope=$1 "
                               "AND requested_at < clock_timestamp()-INTERVAL '48 hours'", scope)
            return True


async def reconcile(scope: str, provider_used: int) -> None:
    """Account for pre-deployment/other callers conservatively, never lower usage.

    The provider reset is account-specific. Baseline units expire only after
    24 hours, so midnight or a larger remaining header cannot reset our guard.
    """
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(hashtext($1))', 'budget:' + scope)
            debt = provider_used - await _used(conn, scope)
            if debt > 0:
                await conn.execute("INSERT INTO sports_api_requests(scope,units,kind) "
                                   "VALUES ($1,$2,'provider_baseline')", scope, debt)


async def budget(scope: str, limit: int) -> dict:
    row = await db.fetchrow("SELECT COALESCE(sum(units),0)::int AS used, "
                            "count(*) FILTER (WHERE kind='request')::int AS app_requests "
                            "FROM sports_api_requests WHERE scope=$1 AND "
                            "requested_at > clock_timestamp()-INTERVAL '24 hours'", scope)
    return {'limit': limit, 'window': 'rolling_24_hours', 'used_estimate': row['used'],
            'application_requests': row['app_requests'], 'remaining': max(0, limit-row['used'])}


async def cached(scope: str, key: str, freshness: int):
    row = await db.fetchrow("SELECT payload FROM sports_api_cache WHERE scope=$1 AND cache_key=$2 "
                            "AND fetched_at > clock_timestamp()-$3*INTERVAL '1 second' "
                            "AND expires_at > clock_timestamp()", scope, key, freshness)
    return row['payload'] if row is not None else None


async def cache(scope: str, key: str, payload: dict) -> None:
    await db.execute("INSERT INTO sports_api_cache(scope,cache_key,payload,expires_at) "
                     "VALUES ($1,$2,$3,clock_timestamp()+INTERVAL '2 days') "
                     "ON CONFLICT(scope,cache_key) DO UPDATE SET payload=EXCLUDED.payload, "
                     "fetched_at=clock_timestamp(),expires_at=EXCLUDED.expires_at",
                     scope, key, payload)
    await db.execute('DELETE FROM sports_api_cache WHERE scope=$1 AND expires_at < clock_timestamp()',
                     scope)


@asynccontextmanager
async def lock(scope: str, name: str):
    async with db.pool().acquire() as conn:
        # Transaction-scoped locks also work through Neon's transaction pooler.
        # Session locks can outlive their owner on a pooled connection.
        async with conn.transaction():
            held = await conn.fetchval('SELECT pg_try_advisory_xact_lock(hashtext($1))',
                                       name + ':' + scope)
            yield held
