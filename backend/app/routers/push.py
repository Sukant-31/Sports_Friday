from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.config import settings
from app.deps import get_current_user_id
from app.logging_conf import get_logger
from app.rate_limit import limiter
from app.repositories import push_subscriptions as push_repo
from app.schemas import PushSubscribe, PushTest, PushUnsubscribe
from app.workers.web_push import send_push

router = APIRouter(prefix="/api/push", tags=["push"])
log = get_logger('push')


@router.post('/test')
@limiter.limit('5/minute')
async def test_notification(request: Request, body: PushTest,
                            user_id: Annotated[UUID, Depends(get_current_user_id)]) -> dict:
    target = await push_repo.find_subscription_for_user(user_id, body.endpoint)
    if target is None:
        raise HTTPException(404, 'No saved push subscription for this browser and account.')
    if settings.push_transport != 'webpush' or not settings.vapid_private_key:
        raise HTTPException(503, 'Real push delivery is not configured on the server.')
    try:
        await send_push(dict(target), {
            'title': 'Sports Friday test',
            'body': 'Your browser push notification is working.',
            'tag': 'sports-friday-push-test',
        })
    except Exception as exc:
        log.warning('Test push delivery failed: %s', type(exc).__name__)
        raise HTTPException(502, 'Push service rejected the test. Check server logs and configuration.') from exc
    # send_push prunes expired targets rather than raising for HTTP 404/410.
    if await push_repo.find_subscription_for_user(user_id, body.endpoint) is None:
        raise HTTPException(410, 'Push subscription expired. Enable notifications again.')
    return {'ok': True, 'status': 'accepted',
            'message': 'Test accepted by the push service. Check your browser notifications.'}


@router.get("/vapid-public-key")
async def vapid_public_key() -> dict:
    return {"key": settings.vapid_public_key}


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
async def subscribe(
    body: PushSubscribe, user_id: Annotated[UUID, Depends(get_current_user_id)]
) -> dict:
    try:
        await push_repo.upsert_push_subscription(
            user_id, body.endpoint, body.keys.p256dh, body.keys.auth
        )
    except push_repo.PushDeviceLimitReached as exc:
        raise HTTPException(409, "Push subscription device limit reached") from exc
    except push_repo.PushOwnershipConflict as exc:
        raise HTTPException(409, 'This browser subscription is still linked to another account. '
                            'Sign out of that account and disable its browser notifications first.') from exc
    return {"ok": True}


@router.delete("/subscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(
    body: PushUnsubscribe, user_id: Annotated[UUID, Depends(get_current_user_id)]
) -> Response:
    await push_repo.delete_push_subscription_by_endpoint(user_id, body.endpoint)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
