"""FastAPI application factory + lifespan wiring."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from limits.errors import StorageError
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app import db
from app.config import settings
from app.cron_auth import require_cron_auth
from app.logging_conf import get_logger
from app.polling_schema import ensure_polling_schema
from app.queue import get_queue
from app.rate_limit import limiter, rate_limit_exceeded, storage_unavailable
from app.redis_client import close_redis
from app.routers import auth, matches, push, subscriptions, teams
from app.security_headers import SecurityHeadersMiddleware, unexpected_error_response
from app.sports_api.client import SportsApiClient
from app.workers.cron_poll import run_once

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    await ensure_polling_schema()
    app.state.sports_client = SportsApiClient()
    app.state.queue = await get_queue()  # arq pool for enqueueing (unused by API today)
    log.info("API started on port %s", settings.api_port)
    yield
    await app.state.sports_client.aclose()
    await close_redis()
    await db.disconnect()


def create_app() -> FastAPI:
    app = FastAPI(title="Sports Notification API", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if request.url.path in ("/api/auth/signup", "/api/auth/login"):
            # Validation errors may echo a password directly or inside the
            # submitted body. Keep error locations/messages, never raw inputs.
            errors = [{key: value for key, value in error.items()
                       if key not in ("input", "ctx")} for error in exc.errors()]
            exc = RequestValidationError(errors)
        return await request_validation_exception_handler(request, exc)

    app.add_exception_handler(Exception, unexpected_error_response)

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded)
    app.add_exception_handler(StorageError, storage_unavailable)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/api/cron/poll")
    async def cron_poll(request: Request, authorization: str | None = Header(default=None)) -> dict:
        """Hit by Vercel Cron (or manually) to run one discovery+poll pass and
        deliver any new notifications inline — see app/workers/cron_poll.py
        for why this replaces the standalone poller+notifier on serverless."""
        require_cron_auth(request, authorization)
        result = await run_once()
        if not result['ok']:
            return JSONResponse(status_code=503, content=result)
        return result

    app.include_router(auth.router)
    app.include_router(teams.router)
    app.include_router(subscriptions.router)
    app.include_router(matches.router)
    app.include_router(push.router)
    return app


app = create_app()
