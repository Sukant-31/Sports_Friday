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


async def find_match_by_external_id(external_id: str) -> asyncpg.Record | None:
    return await db.fetchrow(
        "SELECT * FROM matches WHERE external_id = $1", external_id
    )


async def upsert_match(
    external_id: str,
    home_team_id,
    away_team_id,
    status: str,
    home_score: int,
    away_score: int,
    starts_at,
    minute: int | None,
):
    """Insert a discovered fixture, or refresh its schedule if already known.

    On conflict we deliberately do NOT touch score/minute/status — the poller
    owns live state and runs far more often than discovery, so discovery must
    never stomp a fresher live value with an hour-old snapshot.
    """
    return await db.fetchrow(
        """
        INSERT INTO matches
          (external_id, home_team_id, away_team_id, status, home_score, away_score,
           starts_at, minute)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (external_id) DO UPDATE
          SET home_team_id = EXCLUDED.home_team_id,
              away_team_id = EXCLUDED.away_team_id,
              starts_at = EXCLUDED.starts_at
        RETURNING *
        """,
        external_id,
        home_team_id,
        away_team_id,
        status,
        home_score,
        away_score,
        starts_at,
        minute,
    )


async def update_match_state(
    match_id, status: str, home_score: int, away_score: int, minute: int | None = None
):
    return await db.fetchrow(
        """
        UPDATE matches
        SET status = $2, home_score = $3, away_score = $4, minute = $5,
            last_polled_at = now()
        WHERE id = $1
        RETURNING *
        """,
        match_id,
        status,
        home_score,
        away_score,
        minute,
    )


async def find_live_matches_for_user(user_id) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT DISTINCT m.id, m.external_id, m.status, m.home_score, m.away_score,
               m.minute, ht.name AS home_team, at.name AS away_team, m.starts_at
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        JOIN subscriptions s ON s.team_id IN (m.home_team_id, m.away_team_id)
        WHERE s.user_id = $1 AND m.status IN ('scheduled', 'live')
        ORDER BY m.starts_at
        """,
        user_id,
    )


async def find_match_for_user(user_id, match_id) -> asyncpg.Record | None:
    """A single match (any status) — but only if the user follows one of its
    teams, so the detail view can't expose arbitrary matches."""
    return await db.fetchrow(
        """
        SELECT m.id, m.external_id, m.status, m.home_score, m.away_score, m.minute,
               ht.name AS home_team, at.name AS away_team, m.starts_at
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        WHERE m.id = $1
          AND EXISTS (
            SELECT 1 FROM subscriptions s
            WHERE s.user_id = $2 AND s.team_id IN (m.home_team_id, m.away_team_id)
          )
        """,
        match_id,
        user_id,
    )


async def find_all_events_for_match(match_id) -> list[asyncpg.Record]:
    """Full de-duplicated event timeline for one match, oldest first."""
    return await db.fetch(
        """
        WITH deduped AS (
            SELECT DISTINCT ON (
                     type,
                     COALESCE(detail->>'minute', ''),
                     COALESCE(detail->>'player', '')
                   )
                   type, detail, created_at, id
            FROM match_events
            WHERE match_id = $1
            ORDER BY type,
                     COALESCE(detail->>'minute', ''),
                     COALESCE(detail->>'player', ''), id
        )
        SELECT type, detail, created_at FROM deduped
        ORDER BY created_at ASC, id ASC
        """,
        match_id,
    )


async def find_recent_events_for_matches(
    match_ids: list, per_match_limit: int = 6
) -> list[asyncpg.Record]:
    """Recent events per match for the dashboard feed. Collapses the duplicate
    rows lifecycle events create (one per team) and keeps the newest N."""
    if not match_ids:
        return []
    return await db.fetch(
        """
        WITH deduped AS (
            SELECT DISTINCT ON (
                     match_id, type,
                     COALESCE(detail->>'minute', ''),
                     COALESCE(detail->>'player', '')
                   )
                   match_id, type, detail, created_at, id
            FROM match_events
            WHERE match_id = ANY($1::uuid[])
            ORDER BY match_id, type,
                     COALESCE(detail->>'minute', ''),
                     COALESCE(detail->>'player', ''), id
        )
        SELECT match_id, type, detail, created_at
        FROM (
            SELECT *, ROW_NUMBER() OVER (
                       PARTITION BY match_id ORDER BY created_at DESC, id DESC
                     ) AS rn
            FROM deduped
        ) x
        WHERE rn <= $2
        ORDER BY match_id, created_at DESC, id DESC
        """,
        match_ids,
        per_match_limit,
    )
