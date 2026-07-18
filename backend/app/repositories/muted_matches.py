from __future__ import annotations

from app import db


async def mute(user_id, match_id) -> None:
    await db.execute(
        """
        INSERT INTO muted_matches (user_id, match_id)
        VALUES ($1, $2)
        ON CONFLICT (user_id, match_id) DO NOTHING
        """,
        user_id,
        match_id,
    )


async def unmute(user_id, match_id) -> None:
    await db.execute(
        "DELETE FROM muted_matches WHERE user_id = $1 AND match_id = $2",
        user_id,
        match_id,
    )


async def is_muted(user_id, match_id) -> bool:
    row = await db.fetchrow(
        "SELECT 1 FROM muted_matches WHERE user_id = $1 AND match_id = $2",
        user_id,
        match_id,
    )
    return row is not None


async def list_muted_match_ids(user_id) -> set:
    rows = await db.fetch(
        "SELECT match_id FROM muted_matches WHERE user_id = $1", user_id
    )
    return {r["match_id"] for r in rows}
