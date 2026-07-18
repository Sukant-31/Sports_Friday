from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status

from app.repositories import subscriptions as subs_repo
from app.repositories import teams as teams_repo
from app.schemas import SubscriptionCreate, SubscriptionUpdate


async def list_subscriptions(user_id: UUID) -> list[dict]:
    return [dict(r) for r in await subs_repo.list_subscriptions(user_id)]


async def create(user_id: UUID, payload: SubscriptionCreate) -> dict:
    if not await teams_repo.find_team_by_id(payload.team_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
    row = await subs_repo.create_subscription(
        user_id,
        payload.team_id,
        payload.notify_goals,
        payload.notify_cards,
        payload.notify_match_status,
    )
    return dict(row)


async def update(user_id: UUID, sub_id: UUID, payload: SubscriptionUpdate) -> dict:
    row = await subs_repo.update_subscription(
        user_id,
        sub_id,
        payload.notify_goals,
        payload.notify_cards,
        payload.notify_match_status,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")
    return dict(row)


async def remove(user_id: UUID, sub_id: UUID) -> None:
    if not await subs_repo.delete_subscription(user_id, sub_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")
