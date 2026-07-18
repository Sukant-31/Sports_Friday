from __future__ import annotations

import asyncpg

from app import db


async def upsert_push_subscription(
    user_id, endpoint: str, p256dh: str, auth: str
) -> asyncpg.Record:
    return await db.fetchrow(
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
