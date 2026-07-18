# Repository tree

The concrete directory layout that implements
[`sports-notification-app-architecture.md`](./sports-notification-app-architecture.md).
A **pnpm/npm workspaces monorepo**: one repo, three deployable units (`api`,
`workers`, `web`) plus a shared package. Building it in the phase order from the
architecture doc, files appear roughly top-to-bottom of each package.

```
sports-notification-app/
├── README.md                       # setup, env, "how to run each service"
├── sports-notification-app-architecture.md
├── REPOSITORY_TREE.md              # this file
├── package.json                    # workspaces: ["packages/*"]
├── pnpm-workspace.yaml             # (or npm/yarn workspaces config)
├── .env.example                    # every var from §8, no real secrets
├── .gitignore
├── .dockerignore
├── docker-compose.yml              # local Postgres + Redis (+ optional services)
├── .github/
│   └── workflows/
│       └── ci.yml                  # lint + test + build on push/PR
│
├── packages/
│   │
│   ├── shared/                     # code imported by api AND workers
│   │   ├── package.json
│   │   └── src/
│   │       ├── db/
│   │       │   ├── pool.js         # pg Pool, reads DATABASE_URL
│   │       │   └── queries/        # hand-written SQL helpers (no heavy ORM)
│   │       │       ├── users.js
│   │       │       ├── teams.js
│   │       │       ├── subscriptions.js
│   │       │       ├── matches.js
│   │       │       ├── matchEvents.js      # dedup insert (ON CONFLICT DO NOTHING)
│   │       │       └── pushSubscriptions.js
│   │       ├── redis/
│   │       │   └── client.js       # ioredis connection, reads REDIS_URL
│   │       ├── queue/
│   │       │   └── matchEvents.js  # BullMQ Queue definition ("match-events")
│   │       ├── sportsApi/
│   │       │   ├── client.js       # fetch wrapper: auth header, backoff, circuit breaker
│   │       │   └── normalize.js    # raw API payload -> internal match/event shape
│   │       ├── config.js           # loads & validates env (throws on missing)
│   │       └── logger.js           # pino instance shared everywhere
│   │
│   ├── api/                        # Express/Fastify HTTP server  (Phase 1–2, 5)
│   │   ├── package.json
│   │   ├── src/
│   │   │   ├── index.js            # server bootstrap, listen()
│   │   │   ├── app.js              # app factory: mounts middleware + routes
│   │   │   ├── middleware/
│   │   │   │   ├── auth.js         # verify JWT cookie -> req.user
│   │   │   │   ├── rateLimit.js    # express-rate-limit on auth/search
│   │   │   │   └── errorHandler.js
│   │   │   ├── routes/
│   │   │   │   ├── auth.js         # POST signup/login/logout  (§5)
│   │   │   │   ├── teams.js        # GET /teams/search  (proxy + cache)
│   │   │   │   ├── subscriptions.js# GET/POST/PATCH/DELETE  (§5)
│   │   │   │   ├── matches.js      # GET /matches/live
│   │   │   │   └── push.js         # POST/DELETE /push/subscribe
│   │   │   └── services/           # route-free business logic (testable)
│   │   │       ├── authService.js  # bcrypt hash/verify, JWT sign
│   │   │       ├── teamService.js  # search-then-cache into teams table
│   │   │       └── subscriptionService.js
│   │   └── test/
│   │       ├── auth.test.js
│   │       └── subscriptions.test.js
│   │
│   ├── workers/                    # background processes  (Phase 3–4)
│   │   ├── package.json
│   │   ├── src/
│   │   │   ├── poller.js           # §6 polling worker — interval loop
│   │   │   ├── notifier.js         # §6 notification worker — BullMQ consumer
│   │   │   ├── diff.js             # pure fn: (prevState, nextState) -> events[]
│   │   │   ├── dedupKey.js         # deterministic key builder (see schema note)
│   │   │   └── webPush.js          # web-push setup w/ VAPID keys, send + 410 cleanup
│   │   └── test/
│   │       └── diff.test.js        # core unit test: two snapshots -> exact events
│   │
│   └── web/                        # React + Vite PWA  (Phase 1–2, 4–5)
│       ├── package.json
│       ├── vite.config.js
│       ├── index.html
│       ├── public/
│       │   ├── manifest.webmanifest
│       │   └── icon.png            # notification icon
│       └── src/
│           ├── main.jsx            # app entry, router
│           ├── App.jsx
│           ├── service-worker.js   # §7 push handler -> showNotification
│           ├── registerSW.js       # register SW + request push permission + POST /push/subscribe
│           ├── lib/
│           │   ├── api.js          # fetch wrapper, credentials: 'include'
│           │   └── auth.js         # current-user context/hook
│           ├── pages/
│           │   ├── Login.jsx
│           │   ├── Signup.jsx
│           │   ├── Dashboard.jsx   # live scores for followed teams
│           │   ├── Search.jsx      # find & follow teams
│           │   └── Settings.jsx    # per-team notify_* toggles
│           └── components/
│               ├── TeamCard.jsx
│               ├── MatchTile.jsx
│               └── NotificationToggle.jsx
│
├── migrations/                     # plain SQL, run in order (node-pg-migrate/dbmate)
│   ├── 001_init.sql                # users, teams, subscriptions, matches, push_subscriptions
│   ├── 002_match_events.sql        # match_events ledger (dedup)
│   └── 003_indexes.sql             # hot-path indexes from §4
│
├── scripts/
│   ├── migrate.js                  # apply migrations (used by CI + deploy)
│   ├── gen-vapid-keys.js           # one-off: prints VAPID_PUBLIC/PRIVATE_KEY
│   └── seed.js                     # optional: seed a few teams for local dev
│
└── deploy/
    ├── Dockerfile.api
    ├── Dockerfile.workers
    ├── railway.json                # or render.yaml — backend + Postgres + Redis
    └── vercel.json                 # web deploy config
```

## Mapping to the architecture doc

| Architecture section | Where it lives |
|---|---|
| §3 Client (React PWA) | `packages/web/` |
| §3 Backend API | `packages/api/` |
| §3 Polling worker | `packages/workers/src/poller.js` (+ `diff.js`, `dedupKey.js`) |
| §3 Notification worker | `packages/workers/src/notifier.js` (+ `webPush.js`) |
| §3 Postgres / §4 schema | `migrations/`, queried via `packages/shared/src/db/` |
| §3 Redis + BullMQ | `packages/shared/src/redis/`, `packages/shared/src/queue/` |
| §3 External sports API | `packages/shared/src/sportsApi/` |
| §5 API endpoints | `packages/api/src/routes/` |
| §7 Frontend structure | `packages/web/src/` |
| §8 Env vars | `.env.example`, loaded/validated in `packages/shared/src/config.js` |
| §10 Idempotency ledger | `migrations/002_match_events.sql` + `db/queries/matchEvents.js` + `workers/dedupKey.js` |

## Why this shape

- **`shared/` is the seam that prevents drift.** The poller writes match state and
  the API reads it; both go through the same query helpers and the same sports-API
  normalizer, so "one place defines the data shape" holds.
- **`api` and `workers` are separate packages, not separate folders in one app.**
  This is the §10 design decision — they deploy and scale independently, and you
  can restart the poller without touching the HTTP server.
- **Migrations are plain `.sql`, numbered, forward-only** — reviewable in a PR and
  runnable by the same script locally, in CI, and on deploy.
- **`diff.js` and `dedupKey.js` are pure functions** with no I/O, which is what
  makes the core event-detection logic unit-testable without a live match (§11).
