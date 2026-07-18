#!/usr/bin/env bash
#
# One-command live test: brings up Postgres + Redis, migrates, follows the
# given team(s), and runs the API + poller + notifier (console push) together.
# Optionally starts the frontend too. Ctrl-C stops everything.
#
#   backend/scripts/live_test.sh "Arsenal" "Real Madrid"
#
# Requires SPORTS_API_KEY in ../.env (or the environment). Needs the backend
# venv at backend/.venv (create with: python -m venv .venv && pip install -e ".[dev]").
set -euo pipefail

BACKEND="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$BACKEND/.." && pwd)"
PY="$BACKEND/.venv/bin"
TEAMS=("$@")
[ ${#TEAMS[@]} -eq 0 ] && TEAMS=("Arsenal")

# --- env ---
[ -f "$ROOT/.env" ] && { set -a; . "$ROOT/.env"; set +a; }
export DATABASE_URL="${DATABASE_URL:-postgresql://sports:sports@localhost:5432/sports}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379}"
export JWT_SECRET="${JWT_SECRET:-live-test-secret-please-change-000}"
export PUSH_TRANSPORT="console"                       # deliver to the log, no browser
export DISCOVER_INTERVAL_SECONDS="${DISCOVER_INTERVAL_SECONDS:-600}"  # refresh often during the test
export POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-20}"

[ -x "$PY/python" ] || { echo "backend venv missing at $BACKEND/.venv — see script header"; exit 1; }

# --- pick a container engine for infra ---
ENGINE=""
command -v docker >/dev/null 2>&1 && ENGINE="docker"
[ -z "$ENGINE" ] && command -v podman >/dev/null 2>&1 && ENGINE="podman"

db_reachable() { "$PY/python" - <<'PY' >/dev/null 2>&1
import asyncio, os, asyncpg
async def m():
    c = await asyncpg.connect(os.environ["DATABASE_URL"]); await c.close()
asyncio.run(m())
PY
}

STARTED_INFRA=0
ensure_infra() {
  if db_reachable; then echo "• Postgres reachable — using existing infra"; return; fi
  [ -z "$ENGINE" ] && { echo "Postgres not reachable and no docker/podman found. Start Postgres+Redis manually."; exit 1; }
  echo "• starting Postgres + Redis via $ENGINE"
  $ENGINE run -d --name sf-pg -e POSTGRES_USER=sports -e POSTGRES_PASSWORD=sports \
      -e POSTGRES_DB=sports -p 5432:5432 docker.io/library/postgres:16-alpine >/dev/null
  $ENGINE run -d --name sf-redis -p 6379:6379 docker.io/library/redis:7-alpine >/dev/null
  STARTED_INFRA=1
  for _ in $(seq 1 30); do db_reachable && break; sleep 1; done
}

PIDS=()
cleanup() {
  echo; echo "• stopping services"
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  if [ "$STARTED_INFRA" = "1" ]; then
    echo "• removing infra containers"
    $ENGINE rm -f sf-pg sf-redis >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

# --- bring it up ---
ensure_infra
echo "• applying migrations"
( cd "$BACKEND" && "$PY/python" scripts/migrate.py )
echo "• following teams: ${TEAMS[*]}"
( cd "$BACKEND" && "$PY/python" scripts/live_setup.py "${TEAMS[@]}" )

echo
echo "======================================================================"
echo " starting API (:8000) + poller + notifier   — Ctrl-C to stop"
echo " log in to the dashboard as  demo@local / demo1234"
echo "======================================================================"
echo

cd "$BACKEND"
"$PY/uvicorn" app.main:app --port 8000 --log-level warning & PIDS+=($!)
"$PY/python" -m app.workers.poller & PIDS+=($!)
"$PY/arq" app.workers.notifier.WorkerSettings & PIDS+=($!)

# --- optional frontend (best effort; needs node/npm on PATH) ---
NPM=""
command -v npm >/dev/null 2>&1 && NPM="npm"
if [ -z "$NPM" ]; then
  NODEBIN="$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | tail -1 || true)"
  [ -n "$NODEBIN" ] && [ -x "$NODEBIN/npm" ] && { export PATH="$NODEBIN:$PATH"; NPM="npm"; }
fi
if [ -n "$NPM" ] && [ -d "$ROOT/frontend/node_modules" ]; then
  echo "• starting frontend at http://localhost:5173"
  ( cd "$ROOT/frontend" && "$NPM" run dev ) & PIDS+=($!)
else
  echo "• frontend not started (run 'npm install' in frontend/ and open http://localhost:5173 yourself)"
fi

wait
