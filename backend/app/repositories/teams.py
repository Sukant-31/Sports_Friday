from __future__ import annotations

import asyncpg

from app import db


async def upsert_team(external_id: str, name: str, league: str | None = None) -> asyncpg.Record:
    return await db.fetchrow(
        """
        INSERT INTO teams (external_id, name, league)
        VALUES ($1, $2, $3)
        ON CONFLICT (external_id) DO UPDATE
          SET name = EXCLUDED.name, league = EXCLUDED.league
        RETURNING id, external_id, name, league
        """,
        external_id,
        name,
        league,
    )


async def search_teams_cached(q: str) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT id, external_id, name, league
        FROM teams
        WHERE name ILIKE $1
        ORDER BY name
        LIMIT 20
        """,
        f"%{q}%",
    )


async def find_team_by_id(team_id) -> asyncpg.Record | None:
    return await db.fetchrow(
        "SELECT id, external_id, name, league FROM teams WHERE id = $1", team_id
    )
