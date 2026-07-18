from __future__ import annotations

import asyncpg

from app import db


async def find_pollable_matches() -> list[asyncpg.Record]:
    """Matches worth polling: not finished AND followed by >= 1 user."""
    return await db.fetch(
        """
        SELECT DISTINCT m.id, m.external_id, m.status, m.home_score, m.away_score,
               m.home_team_id, m.away_team_id, m.starts_at, m.last_polled_at
        FROM matches m
        WHERE m.status IN ('scheduled', 'live')
          AND (
            EXISTS (SELECT 1 FROM subscriptions s WHERE s.team_id = m.home_team_id)
            OR EXISTS (SELECT 1 FROM subscriptions s WHERE s.team_id = m.away_team_id)
          )
        """
    )


async def update_match_state(match_id, status: str, home_score: int, away_score: int):
    return await db.fetchrow(
        """
        UPDATE matches
        SET status = $2, home_score = $3, away_score = $4, last_polled_at = now()
        WHERE id = $1
        RETURNING *
        """,
        match_id,
        status,
        home_score,
        away_score,
    )


async def find_live_matches_for_user(user_id) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT DISTINCT m.id, m.external_id, m.status, m.home_score, m.away_score,
               ht.name AS home_team, at.name AS away_team, m.starts_at
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        JOIN subscriptions s ON s.team_id IN (m.home_team_id, m.away_team_id)
        WHERE s.user_id = $1 AND m.status IN ('scheduled', 'live')
        ORDER BY m.starts_at
        """,
        user_id,
    )
