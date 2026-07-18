from __future__ import annotations

import asyncpg

from app import db


async def list_subscriptions(user_id) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT s.id, s.team_id, t.name AS team_name, t.league,
               s.notify_goals, s.notify_cards, s.notify_match_status, s.created_at
        FROM subscriptions s
        JOIN teams t ON t.id = s.team_id
        WHERE s.user_id = $1
        ORDER BY t.name
        """,
        user_id,
    )


async def create_subscription(
    user_id,
    team_id,
    notify_goals: bool = True,
    notify_cards: bool = False,
    notify_match_status: bool = True,
) -> asyncpg.Record:
    return await db.fetchrow(
        """
        INSERT INTO subscriptions
          (user_id, team_id, notify_goals, notify_cards, notify_match_status)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (user_id, team_id) DO UPDATE
          SET notify_goals = EXCLUDED.notify_goals,
              notify_cards = EXCLUDED.notify_cards,
              notify_match_status = EXCLUDED.notify_match_status
        RETURNING *
        """,
        user_id,
        team_id,
        notify_goals,
        notify_cards,
        notify_match_status,
    )


async def update_subscription(
    user_id,
    sub_id,
    notify_goals: bool | None,
    notify_cards: bool | None,
    notify_match_status: bool | None,
) -> asyncpg.Record | None:
    return await db.fetchrow(
        """
        UPDATE subscriptions
        SET notify_goals = COALESCE($3, notify_goals),
            notify_cards = COALESCE($4, notify_cards),
            notify_match_status = COALESCE($5, notify_match_status)
        WHERE id = $1 AND user_id = $2
        RETURNING *
        """,
        sub_id,
        user_id,
        notify_goals,
        notify_cards,
        notify_match_status,
    )


async def delete_subscription(user_id, sub_id) -> bool:
    result = await db.execute(
        "DELETE FROM subscriptions WHERE id = $1 AND user_id = $2", sub_id, user_id
    )
    return result.endswith("1")


async def find_push_targets_for_event(team_id, event_type: str) -> list[asyncpg.Record]:
    """Push targets for a team event, filtered by the users' notify_* prefs.

    event_type is one of: goal | card | kickoff | full_time.
    """
    pref_column = {
        "goal": "s.notify_goals",
        "card": "s.notify_cards",
    }.get(event_type, "s.notify_match_status")  # kickoff | full_time

    return await db.fetch(
        f"""
        SELECT ps.id AS push_id, ps.endpoint, ps.p256dh, ps.auth, s.user_id
        FROM subscriptions s
        JOIN push_subscriptions ps ON ps.user_id = s.user_id
        WHERE s.team_id = $1 AND {pref_column} = true
        """,
        team_id,
    )
