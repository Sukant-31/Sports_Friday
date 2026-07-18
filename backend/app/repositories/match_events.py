from __future__ import annotations

import json

import asyncpg

from app import db


async def record_event_if_new(
    match_id, team_id, event_type: str, detail: dict, dedup_key: str
) -> asyncpg.Record | None:
    """Idempotency ledger. Returns the inserted row, or None if this exact event
    (by dedup_key) was already recorded — the caller then skips enqueueing."""
    return await db.fetchrow(
        """
        INSERT INTO match_events (match_id, team_id, type, detail, dedup_key)
        VALUES ($1, $2, $3, $4::jsonb, $5)
        ON CONFLICT (dedup_key) DO NOTHING
        RETURNING id, match_id, team_id, type, detail
        """,
        match_id,
        team_id,
        event_type,
        json.dumps(detail),
        dedup_key,
    )


async def list_event_keys_for_match(match_id) -> list[str]:
    rows = await db.fetch(
        "SELECT dedup_key FROM match_events WHERE match_id = $1", match_id
    )
    return [r["dedup_key"] for r in rows]
