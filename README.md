# Sports notification app

Real-time push notifications for live football matches. Follow teams, get a
browser/OS notification the moment they score, concede, get a card, kick off,
or finish.

See [`sports-notification-app-architecture.md`](./sports-notification-app-architecture.md)
for the full design and [`REPOSITORY_TREE.md`](./REPOSITORY_TREE.md) for the
layout of this repo.

## Stack

| Part | Tech |
|---|---|
| Backend API | Python + FastAPI (uvicorn), asyncpg, pydantic |
| Workers | asyncio poller + arq notifier |
| Database | PostgreSQL |
| Cache / queue | Redis + arq |
| Push | Web Push (VAPID) via pywebpush |
| Frontend | React + Vite (PWA) |

## Layout

| Path | What it is |
|---|---|
| `backend/` | FastAPI API + poller + notifier (one codebase, three processes) |
| `frontend/` | React + Vite PWA |
| `migrations/` | plain, forward-only SQL |
| `deploy/` | Dockerfile + Render/Vercel configs |

## Prerequisites

- Python ≥ 3.11, Node.js ≥ 20 + [pnpm](https://pnpm.io), Docker

## Getting started

```bash
# 1. Start Postgres + Redis
docker compose up -d

# 2. Configure env
cp .env.example .env

# 3. Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
set -a; source ../.env; set +a        # load env into the shell
python scripts/gen_vapid.py           # paste keys into ../.env
python scripts/migrate.py             # apply migrations
python scripts/seed.py                # optional demo teams

# 4. Run the three backend processes (separate terminals)
uvicorn app.main:app --reload --port 8000        # API
python -m app.workers.poller                     # poller
arq app.workers.notifier.WorkerSettings          # notifier

# 5. Frontend (another terminal)
cd frontend && pnpm install && pnpm dev          # http://localhost:5173
```

## Testing

```bash
cd backend && pytest        # pure diff/dedup unit tests
```

## Notes

- Web Push needs HTTPS in production; `localhost` is exempt for dev.
- Only matches with at least one subscriber are polled (architecture doc, §10).
- Notifications are de-duplicated via the `match_events` ledger, so a restart
  never re-sends an old goal alert.
