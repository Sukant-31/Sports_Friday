from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.config import settings
from app.deps import get_current_user_id
from app.repositories import push_subscriptions as push_repo
from app.schemas import PushSubscribe, PushUnsubscribe

router = APIRouter(prefix="/api/push", tags=["push"])


@router.get("/vapid-public-key")
async def vapid_public_key() -> dict:
    return {"key": settings.vapid_public_key}


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
async def subscribe(
    body: PushSubscribe, user_id: UUID = Depends(get_current_user_id)
) -> dict:
    await push_repo.upsert_push_subscription(
        user_id, body.endpoint, body.keys.p256dh, body.keys.auth
    )
    return {"ok": True}


@router.delete("/subscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(
    body: PushUnsubscribe, user_id: UUID = Depends(get_current_user_id)
) -> Response:
    await push_repo.delete_push_subscription_by_endpoint(user_id, body.endpoint)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
