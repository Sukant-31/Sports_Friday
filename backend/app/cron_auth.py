"""Authenticate cron before any request-triggered polling work."""

import hmac
import os

from fastapi import HTTPException, Request

from app.config import settings


def require_cron_auth(request: Request, authorization: str | None) -> None:
    secret = settings.cron_secret
    if not secret or not secret.strip():
        if settings.is_prod or os.environ.get("VERCEL_ENV") == "production" or secret:
            raise HTTPException(status_code=503, detail="Cron authentication unavailable")
        # Retain the existing local development/test mode without a secret.
        return
    if (len(request.headers.getlist("authorization")) != 1 or authorization is None
            or not hmac.compare_digest(
                authorization.encode(), f"Bearer {secret}".encode()
            )):
        raise HTTPException(status_code=401, detail="unauthorized")
