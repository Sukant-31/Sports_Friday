from __future__ import annotations

import asyncpg

from app import db

MAX_PUSH_ENDPOINTS = 10


class PushDeviceLimitReached(ValueError):
    """A new endpoint would exceed the account's stored-device allowance."""


class PushOwnershipConflict(ValueError):
    """An endpoint is still associated with another account."""


async def find_subscription_for_user(user_id, endpoint: str) -> asyncpg.Record | None:
    return await db.fetchrow(
        "SELECT id AS push_id, endpoint, p256dh, auth FROM push_subscriptions "
        "WHERE user_id = $1 AND endpoint = $2", user_id, endpoint,
    )


async def upsert_push_subscription(
    user_id, endpoint: str, p256dh: str, auth: str
) -> asyncpg.Record:
    async with db.pool().acquire() as conn, conn.transaction():
        # Always lock user first, then endpoint. The user lock serializes
        # different endpoints across connections/serverless instances; the
        # endpoint lock still prevents cross-account ownership races.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 1))",
                           f"push-devices:{user_id}")
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", endpoint)
        if await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM push_subscriptions "
            "WHERE endpoint=$1 AND user_id IS DISTINCT FROM $2::uuid)", endpoint, user_id,
        ):
            raise PushOwnershipConflict("Push endpoint belongs to another account")
        existing = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM push_subscriptions WHERE user_id=$1 AND endpoint=$2)",
            user_id, endpoint,
        )
        if not existing:
            count = await conn.fetchval(
                "SELECT count(*) FROM push_subscriptions WHERE user_id=$1", user_id,
            )
            if count >= MAX_PUSH_ENDPOINTS:
                raise PushDeviceLimitReached("Push subscription device limit reached")
        return await conn.fetchrow(
            """
        INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (user_id, endpoint) DO UPDATE
          SET p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth
        RETURNING id, endpoint
        """,
            user_id,
            endpoint,
            p256dh,
            auth,
        )


async def delete_push_subscription_by_endpoint(user_id, endpoint: str) -> bool:
    result = await db.execute(
        "DELETE FROM push_subscriptions WHERE user_id = $1 AND endpoint = $2",
        user_id,
        endpoint,
    )
    return result.endswith("1")


async def delete_push_subscription_by_id(push_id) -> None:
    """Called by the notifier when Web Push returns 404/410 (expired)."""
    await db.execute("DELETE FROM push_subscriptions WHERE id = $1", push_id)
