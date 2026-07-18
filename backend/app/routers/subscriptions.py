from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.deps import get_current_user_id
from app.schemas import SubscriptionCreate, SubscriptionUpdate
from app.services import subscription_service as svc

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])


@router.get("")
async def list_subscriptions(user_id: UUID = Depends(get_current_user_id)) -> dict:
    return {"subscriptions": await svc.list_subscriptions(user_id)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    body: SubscriptionCreate, user_id: UUID = Depends(get_current_user_id)
) -> dict:
    return {"subscription": await svc.create(user_id, body)}


@router.patch("/{sub_id}")
async def update(
    sub_id: UUID, body: SubscriptionUpdate, user_id: UUID = Depends(get_current_user_id)
) -> dict:
    return {"subscription": await svc.update(user_id, sub_id, body)}


@router.delete("/{sub_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove(sub_id: UUID, user_id: UUID = Depends(get_current_user_id)) -> Response:
    await svc.remove(user_id, sub_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
