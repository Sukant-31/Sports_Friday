# Backend (FastAPI)

FastAPI HTTP API + background workers for the sports notification app.

## Layout

```
app/
  main.py              FastAPI app factory + lifespan
  config.py            pydantic-settings (reads ../.env)
  db.py                asyncpg pool + raw-SQL helpers
  redis_client.py      Redis + per-match state cache
  queue.py             arq queue wiring
  security.py          bcrypt + JWT cookie
  deps.py              get_current_user_id dependency
  rate_limit.py        slowapi limiter
  schemas.py           pydantic request/response models
  sports_api/          HTTP client (backoff + breaker) + normalizer
  repositories/        raw-SQL query functions per table
  routers/             auth, teams, subscriptions, matches, push
  services/            auth / team / subscription business logic
  workers/             diff, dedup_key, poller, notifier, web_push
scripts/               migrate, seed, gen_vapid
tests/                 pure unit tests (diff, dedup_key)
```

## Run locally

```bash
# from repo root: docker compose up -d   (Postgres + Redis)
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# env lives at the repo root (.env); load it into your shell, e.g.:
set -a; source ../.env; set +a

python scripts/migrate.py           # apply migrations
python scripts/gen_vapid.py         # -> paste keys into ../.env
python scripts/seed.py              # optional demo teams

# three processes (separate terminals):
uvicorn app.main:app --reload --port 8000       # API
python -m app.workers.poller                    # poller
arq app.workers.notifier.WorkerSettings         # notifier
```

## Test

```bash
pytest        # runs the pure diff/dedup unit tests
```

Full auth/subscription integration tests run in CI against a throwaway Postgres.
