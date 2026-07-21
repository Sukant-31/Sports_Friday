"""Web Push sending via pywebpush. On an expired subscription (404/410) the
row is pruned and the call resolves; other failures raise so arq retries."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from pywebpush import WebPushException, webpush

from app.config import settings
from app.logging_conf import get_logger
from app.repositories import push_subscriptions as push_repo

log = get_logger("web_push")


def _vapid_claims(endpoint: str) -> dict[str, str]:
    # 'aud' must be the push service's own origin (e.g. https://fcm.googleapis.com),
    # not ours — it's derived per-endpoint, not a fixed value.
    parts = urlsplit(endpoint)
    return {"sub": settings.vapid_subject, "aud": f"{parts.scheme}://{parts.netloc}"}


async def send_push(target: dict[str, Any], payload: dict[str, Any]) -> None:
    # Console transport: log instead of sending. Lets the full poller/notifier
    # flow run locally without a real browser push subscription.
    if settings.push_transport == "console":
        log.info(
            "PUSH → %s  |  %s — %s",
            target.get("endpoint"),
            payload.get("title"),
            payload.get("body"),
        )
        return

    if not settings.vapid_private_key:
        raise RuntimeError("VAPID keys are not set — run scripts/gen_vapid.py")

    subscription = {
        "endpoint": target["endpoint"],
        "keys": {"p256dh": target["p256dh"], "auth": target["auth"]},
    }
    try:
        # pywebpush is sync; it's a short network call. For high volume, offload
        # to a thread pool (asyncio.to_thread) — fine as-is for v1.
        webpush(
            subscription_info=subscription,
            data=json.dumps(payload),
            vapid_private_key=settings.vapid_private_key,
            vapid_claims=_vapid_claims(target["endpoint"]),
        )
    except WebPushException as exc:
        status_code = getattr(exc.response, "status_code", None)
        if status_code in (404, 410):
            log.info("pruning expired push subscription %s", target.get("push_id"))
            await push_repo.delete_push_subscription_by_id(target["push_id"])
            return
        raise
