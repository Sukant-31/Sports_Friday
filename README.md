# Sports notification app

Real-time push notifications for live football matches. Follow teams, get a
browser/OS notification the moment they score, concede, get a card, kick off,
or finish.

See [`sports-notification-app-architecture.md`](./sports-notification-app-architecture.md)
for the full design and [`REPOSITORY_TREE.md`](./REPOSITORY_TREE.md) for the
layout of this repo.

## Monorepo layout

| Package | What it is |
|---|---|
| `packages/shared` | DB pool + queries, Redis, BullMQ queue, sports-API client, config, logger |
| `packages/api` | Express HTTP API (auth, subscriptions, teams, matches, push) |
| `packages/workers` | Poller (detects events) + notifier (sends Web Push) |
| `packages/web` | React + Vite PWA |

## Prerequisites

- Node.js ≥ 20 and [pnpm](https://pnpm.io) (`npm i -g pnpm`)
- Docker (for local Postgres + Redis)

## Getting started

```bash
# 1. Install all workspace deps
pnpm install

# 2. Start Postgres + Redis
docker compose up -d

# 3. Configure env
cp .env.example .env
pnpm gen:vapid          # prints VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY -> paste into .env
#   also set SPORTS_API_KEY and a random JWT_SECRET

# 4. Run migrations
pnpm migrate

# 5. (optional) seed a few teams
pnpm seed

# 6. Run the services (separate terminals)
pnpm dev:api            # http://localhost:4000
pnpm dev:workers        # poller + notifier
pnpm dev:web            # http://localhost:5173
```

## Testing

```bash
pnpm test               # runs tests across all packages
```

The core event-detection logic (`packages/workers/src/diff.js`) is covered by
pure unit tests that don't need a live match.

## Notes

- Web Push needs HTTPS in production; `localhost` is exempt for dev.
- Only matches with at least one subscriber are polled — see the architecture
  doc, §10.
- Notifications are de-duplicated via the `match_events` ledger, so a restart
  never re-sends an old goal alert.
