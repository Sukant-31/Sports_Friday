from __future__ import annotations

import asyncpg

from app import db


async def upsert_team(external_id: str, name: str, league: str | None = None) -> asyncpg.Record:
    return await db.fetchrow(
        """
        INSERT INTO teams (external_id, name, league)
        VALUES ($1, $2, $3)
        ON CONFLICT (external_id) DO UPDATE
          SET name = CASE WHEN EXCLUDED.name = EXCLUDED.external_id
                          THEN teams.name ELSE EXCLUDED.name END,
              league = COALESCE(EXCLUDED.league, teams.league)
        RETURNING id, external_id, name, league
        """,
        external_id,
        name,
        league,
    )


async def search_teams_cached(q: str) -> list[asyncpg.Record]:
    from app.services.team_search import normalize_query

    query = normalize_query(q)
    if len(query) < 2:
        return []
    args = (
        query,
        f"%{query}%",
        query.split(),
        f"%{max(query.split(), key=len)}%",
        r"\m" + r"\w*\M.*\m".join(query.split()),
    )
    try:
        return await db.fetch(
            """
        SELECT id, external_id, name, league
        FROM teams
        WHERE lower(name) LIKE $2
           OR lower(name) % $1
           OR $1 <% lower(name)
           OR (lower(name) LIKE $4 AND
               (SELECT bool_and(lower(name) LIKE '%' || token || '%')
                FROM unnest($3::text[]) AS token))
        ORDER BY CASE
            WHEN lower(name) = $1 THEN 0
            WHEN lower(name) LIKE $1 || '%' THEN 1
            WHEN lower(name) ~ $5 THEN 2
            WHEN lower(name) LIKE $2 THEN 3
            ELSE 4 END,
            word_similarity($1, lower(name)) DESC, lower(name), id
        LIMIT 250
        """,
            *args,
        )
    except asyncpg.UndefinedFunctionError:
        # Older databases may not yet have migration 007's pg_trgm extension.
        # Keep normal search available and let Python rank fuzzy candidates.
        return await db.fetch(
            """
            SELECT id, external_id, name, league
            FROM teams
            WHERE lower(name) LIKE $2
               OR lower(name) LIKE $4
               OR EXISTS (
                   SELECT 1 FROM unnest($3::text[]) AS token
                   WHERE length(token) >= 3
                     AND lower(name) LIKE '%' || left(token, 2) || '%'
               )
            ORDER BY CASE
                WHEN lower(name) = $1 THEN 0
                WHEN lower(name) LIKE $1 || '%' THEN 1
                WHEN lower(name) ~ $5 THEN 2
                WHEN lower(name) LIKE $2 THEN 3
                ELSE 4 END,
                lower(name), id
            LIMIT 250
            """,
            *args,
        )


async def find_team_by_id(team_id) -> asyncpg.Record | None:
    return await db.fetchrow(
        "SELECT id, external_id, name, league FROM teams WHERE id = $1", team_id
    )


async def find_subscribed_teams() -> list[asyncpg.Record]:
    """Teams that at least one user follows — the set discovery pulls fixtures for."""
    return await db.fetch(
        """
        SELECT DISTINCT t.id, t.external_id, t.name
        FROM teams t
        JOIN subscriptions s ON s.team_id = t.id
        """
    )


async def find_teams_needing_name_repair() -> list[asyncpg.Record]:
    return await db.fetch("SELECT id, external_id FROM teams WHERE name = external_id")
