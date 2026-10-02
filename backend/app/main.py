"""FastAPI application factory + lifespan wiring."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app import db
from app.config import settings
from app.logging_conf import get_logger
from app.queue import get_queue
from app.rate_limit import limiter
from app.redis_client import close_redis
from app.routers import auth, matches, push, subscriptions, teams
from app.sports_api.client import SportsApiClient
from app.workers.cron_poll import run_once

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    app.state.sports_client = SportsApiClient()
    app.state.queue = await get_queue()  # arq pool for enqueueing (unused by API today)
    log.info("API started on port %s", settings.api_port)
    yield
    await app.state.sports_client.aclose()
    await close_redis()
    await db.disconnect()


def create_app() -> FastAPI:
    app = FastAPI(title="Sports Notification API", lifespan=lifespan)

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/api/cron/poll")
    async def cron_poll(authorization: str | None = Header(default=None)) -> dict:
        """Hit by Vercel Cron (or manually) to run one discovery+poll pass and
        deliver any new notifications inline — see app/workers/cron_poll.py
        for why this replaces the standalone poller+notifier on serverless."""
        if settings.cron_secret and authorization != f"Bearer {settings.cron_secret}":
            raise HTTPException(status_code=401, detail="unauthorized")
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


def _rate_limit_handler(request, exc):
    return JSONResponse(
        status_code=429, content={"error": "Too many requests, try again later"}
    )


app = create_app()
