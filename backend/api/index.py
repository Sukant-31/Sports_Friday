"""Vercel Python entrypoint. All routes are already prefixed with /api/... in
the routers themselves, so this ASGI app is mounted as-is behind the
catch-all rewrite in vercel.json."""

from app.main import app  # noqa: F401
