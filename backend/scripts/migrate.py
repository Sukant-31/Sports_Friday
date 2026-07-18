"""Minimal forward-only migration runner. Applies every migrations/*.sql not
yet recorded in schema_migrations, in filename order, each in its own
transaction. No down migrations by design.

Run:  python scripts/migrate.py   (from the backend/ dir, with DATABASE_URL set)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

# migrations/ lives at the repo root, one level above backend/
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


async def run() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT now()
            )
            """
        )
        applied = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
        files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql"))

        count = 0
        for path in files:
            if path.name in applied:
                continue
            sql = path.read_text()
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (name) VALUES ($1)", path.name
                )
            print(f"applied {path.name}")
            count += 1

        print("already up to date" if count == 0 else f"applied {count} migration(s)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
