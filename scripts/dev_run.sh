#!/usr/bin/env bash
# Local development stack:
#   1. postgres + redis (docker compose)
#   2. backend (uvicorn, background)
#   3. agent worker (foreground; Ctrl-C to stop everything)
#
# Usage:
#   ./scripts/dev_run.sh
#
# Requires a populated .env at the project root with at minimum:
#   LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET,
#   DEEPGRAM_API_KEY, CARTESIA_API_KEY, GROQ_API_KEY.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [ ! -f .env ]; then
  echo "ERROR: .env not found. Copy .env.example and fill in API keys."
  exit 1
fi

CHILD_PIDS=()

cleanup() {
  echo ""
  echo "==> shutting down…"
  for pid in "${CHILD_PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  echo "==> backend stopped. (postgres + redis left running — 'docker compose -f infra/docker-compose.yml down' to stop them.)"
}
trap cleanup EXIT INT TERM

echo "==> bringing up postgres + redis"
docker compose -f infra/docker-compose.yml up -d postgres redis >/dev/null

echo "==> waiting for postgres health"
for _ in $(seq 1 30); do
  if docker exec twocare-postgres pg_isready -U twocare >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "==> running migrations (idempotent)"
uv run alembic upgrade head

echo "==> starting backend on :8000"
uv run twocare-backend &
CHILD_PIDS+=($!)

echo "==> waiting for backend health"
for _ in $(seq 1 30); do
  if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

mkdir -p logs

echo "==> starting agent worker (Ctrl-C to stop)"
echo "    Connect via https://agents-playground.livekit.io"
exec uv run python -m agent.main dev
