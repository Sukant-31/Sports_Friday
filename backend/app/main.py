"""FastAPI application factory + lifespan wiring."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

    app.include_router(auth.router)
    app.include_router(teams.router)
    app.include_router(subscriptions.router)
    app.include_router(matches.router)
    app.include_router(push.router)
    return app


def _rate_limit_handler(request, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=429, content={"error": "Too many requests, try again later"}
    )


app = create_app()
