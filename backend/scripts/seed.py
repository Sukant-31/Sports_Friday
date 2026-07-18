"""Optional: seed a few teams so local dev has something to follow without
hitting the sports API. Idempotent (upsert by external_id)."""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

TEAMS = [
    ("42", "Arsenal", "Premier League"),
    ("50", "Manchester City", "Premier League"),
    ("40", "Liverpool", "Premier League"),
    ("541", "Real Madrid", "La Liga"),
    ("529", "Barcelona", "La Liga"),
]


async def run() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(database_url)
    try:
        for external_id, name, league in TEAMS:
            await conn.execute(
                """
                INSERT INTO teams (external_id, name, league)
                VALUES ($1, $2, $3)
                ON CONFLICT (external_id) DO UPDATE
                  SET name = EXCLUDED.name, league = EXCLUDED.league
                """,
                external_id,
                name,
                league,
            )
        print(f"seeded {len(TEAMS)} teams")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
