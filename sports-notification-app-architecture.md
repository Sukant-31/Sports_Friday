# Sports notification app — architecture & build plan

**Purpose of this document:** a complete implementation spec an AI coding agent (or a developer) can follow top to bottom to build this project without needing more context. Follow the phases in order — each one produces a working, testable slice.

---

## 1. Product summary

A web app where users follow specific teams/players in one sport (football/soccer to start) and get real-time push notifications when something happens in a live match — goal, red card, kickoff, full-time.

**Non-goals for v1:** multi-sport support, native mobile app, historical stats/analytics. Keep scope tight.

---

## 2. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React + Vite, plain CSS or Tailwind | Fast dev loop, PWA-ready |
| Backend API | **Python + FastAPI (uvicorn)** | Async, first-class typing/validation (pydantic), great DX |
| DB access | **asyncpg + raw SQL** | Async, fast, keeps migrations as plain SQL (no heavy ORM) |
| Validation | **pydantic v2** | Request/response models and settings validation |
| Database | PostgreSQL | Relational data (users, subscriptions, matches) fits well |
| Cache / Queue | **Redis + arq** | Redis caches last-seen match state; arq is the async job queue |
| Push delivery | Web Push API (VAPID) via **pywebpush** | No third-party account needed, works in any browser; swap for FCM later if a native app is added |
| Sports data | API-Football (or TheSportsDB for a free tier) via **httpx** | Reasonable free tier, documented live-event endpoints |
| Auth | JWT (**PyJWT**) with httpOnly cookies + **bcrypt** | Simple, no external auth provider needed |
| Rate limiting | **slowapi** | Blunts credential stuffing + sports-API cost abuse |
| Deployment | Railway / Render (backend + Postgres + Redis), Vercel (frontend) | Free/cheap tiers, minimal ops |

---

## 3. System components

1. **Client (React PWA)** — signup/login, search & follow teams, notification preferences, live match dashboard.
2. **Backend API** — auth, subscription CRUD, exposes match/team search (proxied from sports API).
3. **Polling worker** — background process; every 15–30s, fetches live match data *only for matches with active subscribers*, diffs against last known state, and enqueues detected events.
4. **Notification worker** — consumes the event queue, resolves subscribed users per event, sends push notifications.
5. **PostgreSQL** — persistent data: users, subscriptions, matches, push_subscriptions.
6. **Redis** — arq job queue + short-lived cache of last-seen match state (avoids hitting Postgres on every poll tick).
7. **External sports API** — source of truth for live match data.

Data flow: `Sports API → Polling worker → Redis (arq) queue → Notification worker → Web Push → browser service worker → OS notification`.

The API, poller, and notifier are three separate processes sharing the `backend/app` codebase (FastAPI app, an asyncio poll loop, and an arq worker respectively).

---

## 4. Database schema

```sql
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE teams (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  external_id TEXT UNIQUE NOT NULL,   -- id from sports API
  name TEXT NOT NULL,
  league TEXT
);

CREATE TABLE subscriptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  team_id UUID REFERENCES teams(id) ON DELETE CASCADE,
  notify_goals BOOLEAN DEFAULT true,
  notify_cards BOOLEAN DEFAULT false,
  notify_match_status BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, team_id)
);

CREATE TABLE matches (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  external_id TEXT UNIQUE NOT NULL,
  home_team_id UUID REFERENCES teams(id),
  away_team_id UUID REFERENCES teams(id),
  status TEXT NOT NULL,               -- scheduled | live | finished
  home_score INT DEFAULT 0,
  away_score INT DEFAULT 0,
  starts_at TIMESTAMPTZ,
  last_polled_at TIMESTAMPTZ
);

CREATE TABLE push_subscriptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  endpoint TEXT NOT NULL,
  p256dh TEXT NOT NULL,
  auth TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, endpoint)
);

-- Idempotency ledger: one row per event the poller has ever emitted.
-- The poller inserts here before enqueueing; a unique-violation means the
-- event was already seen (e.g. after a restart or a Redis cache miss) and
-- must NOT be re-notified. This is what prevents duplicate goal alerts.
CREATE TABLE match_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID REFERENCES matches(id) ON DELETE CASCADE,
  team_id UUID REFERENCES teams(id),
  type TEXT NOT NULL,                 -- goal | card | kickoff | full_time
  detail JSONB DEFAULT '{}',
  -- deterministic key derived from the event, e.g.
  -- `${external_match_id}:${type}:${minute}:${scorerExternalId ?? score}`
  dedup_key TEXT UNIQUE NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

**Indexes** (the hot paths — add these in the same migration):
```sql
CREATE INDEX idx_subscriptions_team    ON subscriptions(team_id);
CREATE INDEX idx_subscriptions_user    ON subscriptions(user_id);
CREATE INDEX idx_matches_status        ON matches(status);
CREATE INDEX idx_push_subs_user        ON push_subscriptions(user_id);
CREATE INDEX idx_match_events_match     ON match_events(match_id);
```

---

## 5. API endpoints

```
POST   /api/auth/signup          { email, password }
POST   /api/auth/login           { email, password } -> sets httpOnly cookie
POST   /api/auth/logout

GET    /api/teams/search?q=...   -> proxied search against sports API, cached in `teams` table

GET    /api/subscriptions        -> list current user's followed teams
POST   /api/subscriptions        { team_id, notify_goals, notify_cards, notify_match_status }
PATCH  /api/subscriptions/:id    -> update preferences
DELETE /api/subscriptions/:id

GET    /api/matches/live         -> live matches for teams the user follows (for dashboard)

POST   /api/push/subscribe       { endpoint, keys: { p256dh, auth } } -> store push_subscription
DELETE /api/push/subscribe       -> remove on unsubscribe
```

---

## 6. Background workers

### Polling worker (`backend/app/workers/poller.py`)
A standalone asyncio loop (`asyncio.sleep(POLL_INTERVAL_SECONDS)`), run as its own process:

1. Query `matches` for rows where `status IN ('scheduled','live')` **and** have at least one row in `subscriptions` for their team — skip matches nobody follows.
2. For each, call the sports API's live-match endpoint (via `httpx`).
3. Compare returned state to the cached state in Redis (`match:{id}:state`). The pure `diff_match(prev, next)` function returns the list of events.
4. On diff, determine event type:
   - score changed → `goal` event (include scorer if API provides it)
   - status changed to `live` → `kickoff` event
   - status changed to `finished` → `full_time` event
   - card data changed → `card` event
5. **Dedup before enqueue.** Build the deterministic `dedup_key` and `INSERT ... ON CONFLICT (dedup_key) DO NOTHING` into `match_events`. If no row was inserted, the event was already handled — skip it. Only on a fresh insert do you enqueue.
6. Enqueue each fresh event as an arq `notify_match_event` job with payload `{ match_event_id, match_id, team_id, type, detail }`.
7. Update Redis cache and the `matches` row.

**Cold start / cache miss:** if `match:{id}:state` is absent in Redis (worker restart, TTL expiry), do **not** treat the entire current state as new events — rehydrate the baseline from the `matches` row (scores/status) and the `match_events` ledger, so only genuinely new events fire. Redis is an optimization; Postgres is the source of truth for "what have we already notified about." First sight of a match (no baseline) emits only lifecycle events, never phantom goals.

### Notification worker (`backend/app/workers/notifier.py`)
An arq worker (`arq app.workers.notifier.WorkerSettings`) consuming `notify_match_event` jobs:

1. For the event's `team_id`, query `subscriptions` joined with `push_subscriptions` for users who want this event type (respect `notify_goals`/`notify_cards`/`notify_match_status`).
2. For each push subscription, send a Web Push message using `pywebpush` and the stored VAPID keys.
3. On a `410 Gone` or `404` response (expired subscription), delete that `push_subscriptions` row.
4. Let arq handle retries with backoff (`max_tries`) on transient send failures. Because enqueue is gated by the `match_events` ledger, a retried job re-notifies the same event to the same users at most once per delivery attempt — acceptable — but a *new* poll tick will never duplicate it.

---

## 7. Frontend structure

```
frontend/src/
  pages/
    Login.jsx
    Signup.jsx
    Dashboard.jsx        -- live matches for followed teams
    Search.jsx            -- find and follow teams
    Settings.jsx           -- per-team notification prefs
  service-worker.js         -- handles push events, shows OS notification
  lib/api.js                -- fetch wrapper with cookie auth
```

Service worker push handler (minimal):
```js
self.addEventListener('push', event => {
  const data = event.data.json();
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/icon.png'
    })
  );
});
```

---

## 8. Environment variables

Loaded by pydantic-settings from a single `.env` at the repo root (see `.env.example`):
```
DATABASE_URL=postgresql://...
REDIS_URL=redis://...
JWT_SECRET=...
AUTH_COOKIE_NAME=sports_token
SPORTS_API_KEY=...
SPORTS_API_BASE_URL=https://v3.football.api-sports.io
VAPID_PUBLIC_KEY=...
VAPID_PRIVATE_KEY=...
VAPID_SUBJECT=mailto:you@example.com
API_PORT=8000
POLL_INTERVAL_SECONDS=20
CORS_ORIGIN=http://localhost:5173
ENV=development
```

---

## 9. Build order (phases)

**Phase 1 — Foundation**
- Set up backend project, Postgres connection, run schema migrations.
- Implement auth endpoints (signup/login/logout with JWT cookie).
- Basic React app scaffold with login/signup forms.

**Phase 2 — Subscriptions**
- Team search endpoint (proxy sports API, cache results into `teams`).
- Subscriptions CRUD endpoints + frontend Search and Settings pages.

**Phase 3 — Polling worker (no notifications yet)**
- Build the poller, log detected events to console instead of the queue.
- Verify it correctly detects goals/status changes on a real live match (test during an actual game, or mock the API response).

**Phase 4 — Queue + notifications**
- Add arq, wire poller to enqueue events.
- Build notifier worker, implement Web Push sending.
- Add push subscription flow on the frontend (request permission, register service worker, POST to `/api/push/subscribe`).

**Phase 5 — Dashboard & polish**
- Live matches dashboard showing followed teams' current scores.
- Error handling, rate-limit backoff for the sports API, basic tests.
- Deploy.

---

## 10. Key implementation notes for the agent

- Only poll matches that have subscribers — check this before every phase-3+ commit; it's the detail most likely to be skipped and it's the difference between a sane API bill and a rate-limit ban.
- Keep the poller and notifier as separate processes/workers, not functions called inline from the API server — this is the core "real-time systems" design decision worth defending in an interview.
- Store the sports API's raw match ID as `external_id` everywhere; never assume it's a UUID or numeric — treat it as an opaque string.
- Web Push requires HTTPS in production (localhost is exempted for dev).
- Hash passwords with `bcrypt` (cost ≥ 12) or `argon2`; never store or log plaintext. Auth cookie: `httpOnly`, `secure` (prod), `sameSite=lax`.
- Every notification path must be idempotent — see the `match_events` ledger. Assume the poller, the queue, and the notifier can each retry; a user should never get two "GOAL!" pushes for the same goal.
- Add backoff + a circuit breaker around the sports API. On `429`/`5xx`, back off exponentially and widen the poll interval; a followed match going un-polled for a tick is fine, a rate-limit ban is not.
- Rate-limit auth and search endpoints (`slowapi`) to blunt credential stuffing and API-cost abuse.
- Bind `uuid` columns with `UUID` objects (not strings) — asyncpg is strict about types; the auth dependency returns a `UUID` for this reason.

---

## 11. Testing strategy

- **Unit (`pytest`):** the poller's `diff_match` / `dedup_key` logic — feed two consecutive API snapshots, assert the exact set of events emitted (and that a repeat snapshot emits nothing). These are pure functions with no I/O.
- **Integration:** auth + subscription CRUD against a throwaway Postgres (the CI Postgres service, or a `docker compose` test db).
- **Contract/mock:** record a real sports-API live-match response and replay it as a fixture so tests don't depend on a live game.
- **E2E (manual for v1):** register a push subscription in a real browser, enqueue a synthetic event, confirm the OS notification fires.

See `REPOSITORY_TREE.md` for the full directory layout that implements this spec.
