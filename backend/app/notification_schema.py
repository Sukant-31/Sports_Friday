"""Upgrade the notification ledger on databases predating migration 006.

Serverless deployments do not run the local migration script. This narrow,
idempotent upgrade preserves 006's historical-event handling and makes cron
safe when that migration was omitted. Other schema migrations stay explicit.
"""
from __future__ import annotations

from app import db
from app.logging_conf import get_logger

log = get_logger('notification_schema')


async def ensure_notification_delivery_schema() -> None:
    async with db.pool().acquire() as conn:
        await _ensure_schema(conn)


async def _ensure_schema(conn) -> None:
    async def ready() -> bool:
        return await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM pg_attribute
                WHERE attrelid = to_regclass('match_events')
                  AND attname = 'delivered_at' AND NOT attisdropped
            ) AND to_regclass('notification_deliveries') IS NOT NULL
        """)

    if await ready():
        return
    async with conn.transaction():
        # Concurrent cron invocations must not race schema creation/backfill.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                           'sports_friday.notification_delivery_schema')
        if await ready():
            return
        await conn.execute("""
            ALTER TABLE match_events
              ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ DEFAULT now();
            ALTER TABLE match_events ALTER COLUMN delivered_at DROP DEFAULT;
            CREATE INDEX IF NOT EXISTS match_events_pending_idx
              ON match_events (created_at) WHERE delivered_at IS NULL;
            CREATE TABLE IF NOT EXISTS notification_deliveries (
              event_id UUID REFERENCES match_events(id) ON DELETE CASCADE,
              push_id UUID REFERENCES push_subscriptions(id) ON DELETE CASCADE,
              PRIMARY KEY (event_id, push_id)
            );
            ALTER TABLE subscriptions ALTER COLUMN notify_cards SET DEFAULT true;
            CREATE TABLE IF NOT EXISTS schema_migrations (
              name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now()
            );
            INSERT INTO schema_migrations (name) VALUES ('006_notification_delivery.sql')
              ON CONFLICT (name) DO NOTHING;
        """)
    log.info('Applied missing notification delivery schema (migration 006)')
