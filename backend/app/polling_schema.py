"""Install the additive polling migration on serverless hosts without a runner."""
from importlib.resources import files

from app import db
from app.logging_conf import get_logger

log = get_logger('polling_schema')


async def ensure_polling_schema() -> None:
    async with db.pool().acquire() as conn:
        if await conn.fetchval("SELECT to_regclass('sports_api_requests') IS NOT NULL "
                               "AND to_regclass('sports_api_cache') IS NOT NULL "
                               "AND EXISTS (SELECT 1 FROM pg_attribute WHERE "
                               "attrelid=to_regclass('teams') AND attname='last_discovered_at') "
                               "AND EXISTS (SELECT 1 FROM pg_attribute WHERE "
                               "attrelid=to_regclass('matches') AND attname='last_checked_at')"):
            return
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('sports_friday.polling_schema'))")
            sql = files('app').joinpath('sql/008_intelligent_polling.sql').read_text()
            await conn.execute(sql)
            await conn.execute("INSERT INTO schema_migrations(name) VALUES "
                               "('008_intelligent_polling.sql') ON CONFLICT DO NOTHING")
        log.info('Applied additive intelligent polling schema (migration 008)')
