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
| `frontend/` | React + Vite PWA (includes `vercel.json` deploy config) |
| `migrations/` | plain, forward-only SQL |
| `deploy/` | Dockerfile + Render config |

## Prerequisites

- Python ≥ 3.11, Node.js ≥ 20, Docker

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
cd frontend && npm install && npm run dev        # http://localhost:5173
```

## Testing

```bash
cd backend && pytest        # unit + (with Postgres/Redis up) integration tests
```

## Live test (real sports API)

With a real `SPORTS_API_KEY` in `.env`, verify the key and then run everything
in one command:

```bash
cd backend
set -a; source ../.env; set +a
.venv/bin/python scripts/check_sports_api.py        # confirm the key works

# brings up Postgres+Redis, migrates, follows the team(s), and runs
# API + poller + notifier (console push) — Ctrl-C stops it all
scripts/live_test.sh "Arsenal" "Real Madrid"
```

It prints upcoming/live fixtures for the followed teams and creates a demo
login (`demo@local` / `demo1234`) you can use on the dashboard. When a followed
match is live, goal/card/kickoff/full-time events print as `PUSH → …` lines.

## Deploying free on Vercel (backend + frontend)

Both `backend/` and `frontend/` are separate Vercel projects.

1. **Data stores**: create a free [Neon](https://neon.tech) Postgres (use the
   pooled connection string) and a free [Upstash](https://upstash.com) Redis
   (TCP connection string, not REST).
2. **Backend**: `vercel` (or link via dashboard) inside `backend/`. Set env
   vars from `.env.example` (`DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`,
   `VAPID_*`, `SPORTS_API_KEY`, `CORS_ORIGIN` = your frontend URL, `ENV=production`,
   and a random `CRON_SECRET`).
3. **Frontend**: update the `destination` in `frontend/vercel.json`'s rewrite
   to your backend's Vercel URL, then `vercel` inside `frontend/`.

**Tradeoff**: Vercel has no always-on process, so there's no continuously
running poller/notifier here — `/api/cron/poll` runs one discovery+poll+notify
pass, triggered by Vercel Cron. Free (Hobby) plan crons run **once a day**, so
live goal/card notifications become a once-daily state check instead of
near-real-time. For real-time push, run the standalone poller/notifier (see
"Getting started" above) on a host with a persistent process, e.g. via
`deploy/render.yaml`.

## Notes

- Web Push needs HTTPS in production; `localhost` is exempt for dev.
- Only matches with at least one subscriber are polled (architecture doc, §10).
- Notifications are de-duplicated via the `match_events` ledger, so a restart
  never re-sends an old goal alert.

## Running live notifications continuously

The daily Vercel cron is a fallback, not a live notification service. Keep the
frontend/API on Vercel if desired, but run both worker commands on an always-on
host with the **same database and Redis** as the API. Set `DATABASE_URL`,
`REDIS_URL`, `JWT_SECRET`, `SPORTS_API_KEY`, and `VAPID_*` on the workers;
use `PUSH_TRANSPORT=webpush`. Apply migrations before starting updated services.
The poller checks every 20 seconds by default; sports-provider request quotas
must accommodate the number of followed fixtures being polled.

For a complete local backend (database, Redis, migrations, API, and both workers),
configure `.env` as above, then run:

```bash
docker compose -f docker-compose.yml -f docker-compose.live.yml up -d --build
```

Run the frontend with `cd frontend && npm run dev`. In the dashboard, choose
**Enable notifications** and allow browser permission. Follow a team and check
its Goals, Cards, and Kickoff / full-time settings. New follows enable all three;
existing preferences are preserved, so enable Cards manually for older follows.
Muted matches do not send alerts. The live-test script uses console delivery;
its `PUSH →` messages are not browser notifications.

Migration `006_notification_delivery.sql` adds durable pending notifications.
Unsuccessful deliveries are retried on subsequent poll passes, including for
finished matches. Successful recipients are remembered across partial failures.
Previously recorded events are treated as historical during migration and are
not replayed. Delivery is at-least-once: a crash after the push service accepts a
message but before its receipt commits can cause a repeat; stable notification
tags help the browser replace repeated alerts.
