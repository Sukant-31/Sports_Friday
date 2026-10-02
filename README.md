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

Vercel runs `/api/cron/poll` as a short DB-first invocation. The built-in
Hobby daily cron remains a backup. For timely notifications, configure the
existing external scheduler to invoke the same authenticated endpoint **every
minute**, retaining its existing Authorization header. An invocation normally
makes **zero provider requests** unless discovery or a relevant fixture is due.
An every-two-hours schedule cannot deliver timely match updates. No scheduler
or secret changes are needed in the code; changing the external interval is an
account-side operation. A persistent worker can use the same polling policy.

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
The worker checks database decisions every 20 seconds by default; provider
requests follow the independent timing and shared budget described below.

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

## Match discovery and data freshness

Following a team discovers today's ongoing and tomorrow's upcoming fixtures
using global date feeds. Free-plan discovery defaults to those two dates;
farther future schedules require provider access to those dates. Previously
stored future fixtures remain available. Discovery is refreshed per team every
six hours, and date responses are cached in PostgreSQL across invocations and
teams. A fixture involving two followed teams is upserted once per pass.
Discovery updates schedules without overwriting fresher scores/status.

The default policy checks a fixture 15 minutes before kickoff, at most every
15 minutes before kickoff. Once kickoff is due, it checks every three minutes.
Live matches share one `GET /fixtures?live=all` request, including embedded
events, regardless of how many teams are followed. Irrelevant fixtures in that
feed are ignored. Fixtures missing from the live feed use one shared
`GET /fixtures?date=YYYY-MM-DD` per date for kickoff/final-status confirmation.
No `next`, `ids`, season filter, or per-team live requests are used by cron.

Expected match duration is 120 minutes, followed by 30 minutes of grace.
Confirmed finished fixtures stop immediately. Missing starts back off after
15 minutes; unresolved final states back off to 30-minute checks, with a
six-hour recovery horizon. Unknown kickoff times have low priority. The
existing durable notification retry queue continues even when no fixture or
API budget is available. The dashboard retains finished matches for 24 hours
and flags stale live data after the configured interval plus two minutes.

## Free-plan request budget and reporting

`MAX_DAILY_API_REQUESTS=90` leaves a ten-request margin below the nominal Free
quota. `API_PRIORITY_RESERVE=15` stops discovery, search, profile repairs, and
medium/low priority checks at 75 estimated used requests. High priority
live/near-kickoff/unresolved important states can use the final 15. All client
HTTP attempts, including retries and ambiguous timeouts, reserve a request
atomically in PostgreSQL before contacting the provider. Cached responses do
not spend requests. Transaction-scoped advisory locks coordinate budgets,
shared-feed fetches, and overlapping cron runs through Neon connection pooling.

The guard uses a **rolling 24 hours**, not midnight UTC: API-Football resets
accounts at their activation time. Provider remaining/limit headers add a
conservative baseline for requests made before deployment or by other callers;
they never lower the local rolling estimate. This can temporarily defer calls
past a provider reset, deliberately favoring quota safety. Requests from tools
outside the app cannot be prevented; the next provider response reconciles
reported usage. Request rows older than 48 hours and expired response-cache
rows are pruned without changing football or notification records.

Typical estimates with eight followed teams and a one-minute cron:

| Situation | Provider requests |
|---|---|
| Idle invocation, fresh discovery | 0 |
| Discovery refresh, any number of followed teams | At most 2 shared date requests |
| No match day | About 8/day for discovery |
| One two-hour live window, including overlapping matches | About 40 live requests, plus discovery and kickoff/final checks; roughly 50–60/day |
| Multiple non-overlapping live windows | Higher; the 90-request guard stops further requests |

Searches, new follows, retries, postponed matches, and other API clients affect
these estimates. A 100-request plan cannot guarantee immediate alerts for
unlimited sequential matches. Date feeds may omit detailed event lists; final
confirmation still detects score/status changes, but late cards cannot be
recovered without provider event details. Browser display also depends on the
browser/OS and push service; accepted delivery receipts are not display proof.

Cron summaries include followed-team/stored/live/due counts, priorities,
HTTP attempts/made/skipped/cache hits and skip reasons, estimated budget,
events generated, committed recipient deliveries, failed notification
operations, and safe error categories. Intentional skips return HTTP 200 with
`nothing_due`, `budget_exhausted`, `priority_reserve`, or `skipped` (overlap).
Real API, database, notification, and processing failures return HTTP 503 and
an accurate partial/error summary. No exception messages or secrets appear in
responses.

Migration `008_intelligent_polling.sql` adds request accounting/cache tables,
`teams.last_discovered_at`, and `matches.last_checked_at`. It is additive and
idempotent. The API installs the packaged migration on startup when missing;
the regular migration runner also records/applies it. The packaged SQL is
checked against the canonical migration in tests.

The authenticated, browser-owned `POST /api/push/test` remains available for
future delivery troubleshooting, with its existing five-per-minute rate limit.
No subscription, VAPID, service-worker, preference, recipient-selection, or
delivery-tracking behavior has changed.

Score increases produce one event per goal, including when provider event
details are unavailable. Event identity survives reordered provider lists;
late historical goal details do not send a second alert. Timeline queries
collapse only kickoff/full-time fan-out rows, preserving distinct goals.
Provider score reductions update the scoreboard without creating a new goal
alert; previously sent alerts cannot be recalled.

Discovery automatically repairs team names that were overwritten with provider
IDs, using verified provider profiles. Unknown profiles are left untouched and
retried on the next discovery pass. Fixture updates preserve known league data.

Search and follow responses include warnings when live data cannot be fetched.
The dashboard and match details also warn about missing sports configuration
or live scores that have not been polled for more than two minutes.
