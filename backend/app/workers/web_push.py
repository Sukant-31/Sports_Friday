"""Web Push sending via pywebpush. On an expired subscription (404/410) the
row is pruned. Provider acceptance is not confirmation of browser display.
Permanent rejections are distinguished from retryable failures."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal
from urllib.parse import urlsplit

from pywebpush import WebPushException, webpush
from requests import Session

from app.config import settings
from app.logging_conf import get_logger
from app.push_destination import validate_push_destination
from app.repositories import push_subscriptions as push_repo

log = get_logger("web_push")


class _PushSession(Session):
    """Check the actual outgoing URL and never follow provider redirects."""

    def request(self, method, url, **kwargs):
        validate_push_destination(url)
        kwargs["allow_redirects"] = False
        response = super().request(method, url, **kwargs)
        if 300 <= response.status_code < 400:
            raise WebPushException("Push service redirect refused", response=response)
        return response


def _send(subscription: dict, payload: dict) -> None:
    with _PushSession() as session:
        webpush(
            subscription_info=subscription,
            data=json.dumps(payload),
            timeout=10,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims=_vapid_claims(subscription["endpoint"]),
            requests_session=session,
        )


def _vapid_claims(endpoint: str) -> dict[str, str]:
    # 'aud' must be the push service's own origin (e.g. https://fcm.googleapis.com),
    # not ours — it's derived per-endpoint, not a fixed value.
    parts = urlsplit(endpoint)
    return {"sub": settings.vapid_subject, "aud": f"{parts.scheme}://{parts.netloc}"}


class InvalidPushSubscription(ValueError):
    """Subscription input that cannot be retried unchanged."""


class PermanentPushError(WebPushException):
    """A rejected request that must not be automatically retried unchanged."""

    def __init__(self, status_code: int):
        message = "Push service redirect refused" if 300 <= status_code < 400 else "Push service permanently rejected request"
        super().__init__(message)
        self._status_code = status_code

    @property
    def status_code(self) -> int:
        # Newer pywebpush versions expose a read-only property with this name.
        # Keep our sanitized error's status independent of the provider response.
        return self._status_code


async def send_push(target: dict[str, Any], payload: dict[str, Any]) -> Literal["accepted", "expired", "simulated"]:
    # Console transport: log instead of sending. Lets the full poller/notifier
    # flow run locally without a real browser push subscription.
    if settings.push_transport == "console":
        log.info(
            "PUSH → %s  |  %s — %s",
            target.get("endpoint"),
            payload.get("title"),
            payload.get("body"),
        )
        return "simulated"

    try:
        validate_push_destination(target["endpoint"])
    except ValueError as exc:
        raise InvalidPushSubscription("Invalid push destination") from exc
    if not settings.vapid_private_key:
        raise RuntimeError("VAPID keys are not set — run scripts/gen_vapid.py")

    subscription = {
        "endpoint": target["endpoint"],
        "keys": {"p256dh": target["p256dh"], "auth": target["auth"]},
    }
    try:
        # Keep synchronous network I/O off the worker event loop.
        await asyncio.to_thread(_send, subscription, payload)
    except WebPushException as exc:
        status_code = getattr(exc.response, "status_code", None)
        if status_code is None and exc.message in {
            "Invalid p256dh key specified", "No keys specified in subscription info",
            "Missing keys value: p256dh", "Missing keys value: auth",
        }:
            raise InvalidPushSubscription("Invalid push subscription keys") from exc
        if status_code in (404, 410):
            log.info("pruning expired push subscription %s", target.get("push_id"))
            await push_repo.delete_push_subscription_by_id(target["push_id"])
            return "expired"
        if status_code is not None and (300 <= status_code < 400 or
                                       400 <= status_code < 500 and status_code not in (408, 425, 429)):
            raise PermanentPushError(status_code) from exc
        raise
    return "accepted"
