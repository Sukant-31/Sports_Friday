# Repository tree

The concrete directory layout that implements
[`sports-notification-app-architecture.md`](./sports-notification-app-architecture.md).
Two top-level apps — a **Python/FastAPI `backend/`** (API + workers, one codebase,
three processes) and a **React/Vite `frontend/`** — with language-agnostic SQL
migrations shared between them.

```
sports-notification-app/
├── README.md                       # setup + how to run each service
├── sports-notification-app-architecture.md
├── REPOSITORY_TREE.md              # this file
├── .env.example                    # every var from §8, no real secrets
├── .gitignore
├── .dockerignore
├── docker-compose.yml              # local Postgres + Redis
├── .github/
│   └── workflows/
│       └── ci.yml                  # backend (pytest) + frontend (build) jobs
│
├── migrations/                     # plain SQL, forward-only, run in order
│   ├── 001_init.sql                # users, teams, subscriptions, matches, push_subscriptions
│   ├── 002_match_events.sql        # match_events ledger (dedup)
│   ├── 003_indexes.sql             # hot-path indexes from §4
│   └── 004_match_minute.sql        # live match minute (dashboard)
│
├── backend/                        # Python 3.11+ — FastAPI API + workers
│   ├── pyproject.toml              # deps + pytest/ruff config
│   ├── README.md
│   ├── app/
│   │   ├── main.py                 # FastAPI app factory + lifespan (pool, http client, arq)
│   │   ├── config.py               # pydantic-settings, reads ../.env
│   │   ├── logging_conf.py
│   │   ├── db.py                   # asyncpg pool + fetch/fetchrow/execute helpers
│   │   ├── redis_client.py         # Redis + match:{id}:state cache (get/set)
│   │   ├── queue.py                # arq pool + enqueue_match_event
│   │   ├── security.py             # bcrypt hash/verify, JWT sign/verify, cookie opts
│   │   ├── deps.py                 # get_current_user_id (returns UUID from cookie)
│   │   ├── rate_limit.py           # slowapi limiter
│   │   ├── schemas.py              # pydantic request/response models
│   │   ├── sports_api/
│   │   │   ├── client.py           # httpx client: auth, backoff, circuit breaker
│   │   │   └── normalize.py        # raw API payload -> internal match/event shape
│   │   ├── repositories/           # raw-SQL query functions, one module per table
│   │   │   ├── users.py
│   │   │   ├── teams.py
│   │   │   ├── subscriptions.py    # incl. find_push_targets_for_event
│   │   │   ├── matches.py
│   │   │   ├── match_events.py     # record_event_if_new (ON CONFLICT DO NOTHING)
│   │   │   └── push_subscriptions.py
│   │   ├── routers/                # FastAPI routers (§5 endpoints)
│   │   │   ├── auth.py             # signup / login / logout
│   │   │   ├── teams.py            # GET /teams/search
│   │   │   ├── subscriptions.py    # CRUD
│   │   │   ├── matches.py          # GET /matches/live
│   │   │   └── push.py             # vapid key + subscribe/unsubscribe
│   │   ├── services/               # business logic (testable, framework-light)
│   │   │   ├── auth_service.py
│   │   │   ├── team_service.py     # search-then-cache into teams table
│   │   │   └── subscription_service.py
│   │   └── workers/
│   │       ├── diff.py             # pure fn: (prev, next) -> events[]
│   │       ├── dedup_key.py        # deterministic key builder
│   │       ├── discovery.py        # fetch fixtures for subscribed teams -> matches
│   │       ├── poller.py           # asyncio loop (discovery + poll); python -m app.workers.poller
│   │       ├── notifier.py         # arq WorkerSettings; run: arq app.workers.notifier.WorkerSettings
│   │       └── web_push.py         # pywebpush send + 404/410 cleanup
│   ├── scripts/
│   │   ├── migrate.py              # apply migrations/ (asyncpg, forward-only)
│   │   ├── gen_vapid.py            # print a VAPID key pair for .env
│   │   ├── check_sports_api.py     # verify a real SPORTS_API_KEY works
│   │   ├── demo_flow.py            # mock-data poller→notifier demo
│   │   ├── live_setup.py           # follow team(s) + discover fixtures (live test)
│   │   ├── live_test.sh            # one-command live test: infra + all services
│   │   └── seed.py                 # optional demo teams
│   └── tests/
│       ├── test_diff.py            # core unit test: snapshots -> exact events
│       └── test_dedup_key.py       # dedup key stability/uniqueness
│
├── frontend/                       # React + Vite PWA
│   ├── package.json
│   ├── vite.config.js              # dev proxy /api -> http://localhost:8000
│   ├── index.html
│   ├── public/
│   │   ├── manifest.webmanifest
│   │   └── icon.png                # (add real asset)
│   └── src/
│       ├── main.jsx                # app entry, router
│       ├── App.jsx
│       ├── styles.css
│       ├── service-worker.js       # §7 push handler -> showNotification
│       ├── registerSW.js           # register SW + request permission + POST /push/subscribe
│       ├── lib/
│       │   ├── api.js              # fetch wrapper, credentials: 'include'
│       │   └── auth.jsx            # auth context/hook
│       ├── pages/
│       │   ├── Login.jsx
│       │   ├── Signup.jsx
│       │   ├── Dashboard.jsx       # live matches: polling, minute, event feed
│       │   ├── Search.jsx          # find & follow teams
│       │   └── Settings.jsx        # per-team notify_* toggles
│       └── components/
│           ├── TeamCard.jsx
│           ├── MatchTile.jsx       # score + status badge + score-flash
│           ├── EventFeed.jsx       # recent goals/cards/kickoff/full-time
│           └── NotificationToggle.jsx
│
└── deploy/
    ├── Dockerfile.backend          # one image; CMD chooses api | poller | notifier
    ├── render.yaml                 # api + poller + notifier + Postgres + Redis
    └── vercel.json                 # frontend deploy config
```

## Mapping to the architecture doc

| Architecture section | Where it lives |
|---|---|
| §3 Client (React PWA) | `frontend/` |
| §3 Backend API | `backend/app/main.py` + `routers/` + `services/` |
| §3 Polling worker | `backend/app/workers/poller.py` (+ `diff.py`, `dedup_key.py`) |
| §3 Notification worker | `backend/app/workers/notifier.py` (+ `web_push.py`) |
| §3 Postgres / §4 schema | `migrations/`, queried via `backend/app/repositories/` |
| §3 Redis + arq | `backend/app/redis_client.py`, `backend/app/queue.py` |
| §3 External sports API | `backend/app/sports_api/` |
| §5 API endpoints | `backend/app/routers/` |
| §7 Frontend structure | `frontend/src/` |
| §8 Env vars | `.env.example`, loaded/validated in `backend/app/config.py` |
| §10 Idempotency ledger | `migrations/002_match_events.sql` + `repositories/match_events.py` + `workers/dedup_key.py` |

## Why this shape

- **API, poller, and notifier are three processes over one `backend/app` package.**
  They share the pool, repositories, and sports-API client, but deploy and scale
  independently — the §10 "separate real-time workers" decision. The Dockerfile
  builds one image; the start command selects the role.
- **`repositories/` isolates all SQL.** Raw asyncpg keeps the queries readable and
  the migrations plain, honoring the "no heavy ORM" intent; services and routers
  never write SQL inline.
- **`diff.py` and `dedup_key.py` are pure functions** with no I/O — the core
  event-detection logic is unit-testable without a live match (§11).
- **Migrations are plain, numbered, forward-only `.sql`** at the repo root so both
  the app and CI apply them with the same `scripts/migrate.py`.
- **The frontend is unchanged by the backend swap** — it talks to the same
  `/api/*` contract; only the dev proxy target moved to port 8000.
