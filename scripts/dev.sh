#!/usr/bin/env bash
# Local dev launcher — no docker required.
# Starts redis (if installed) + backend (uvicorn) + frontend (next dev) in foreground.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[dev] root=$ROOT"

# 1. .env
if [ ! -f .env ]; then
  echo "[dev] copying .env.example -> .env"
  cp .env.example .env
fi

# 2. Python venv
if [ ! -d .venv ]; then
  echo "[dev] creating .venv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -e ".[dev]"

# 3. Redis (best-effort)
if command -v redis-server >/dev/null 2>&1; then
  if ! redis-cli ping >/dev/null 2>&1; then
    echo "[dev] starting redis-server in background"
    redis-server --daemonize yes
  fi
else
  echo "[dev] WARN: redis-server not installed; event_bus will degrade to in-process pub/sub"
fi

# 4. Backend
export PYTHONPATH="$ROOT/backend"
echo "[dev] starting backend on :8000"
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# 5. Frontend
if [ -d frontend ]; then
  cd frontend
  if [ ! -d node_modules ]; then
    echo "[dev] installing frontend deps"
    npm install --legacy-peer-deps
  fi
  echo "[dev] starting frontend on :3000"
  npm run dev &
  FRONTEND_PID=$!
  cd "$ROOT"
fi

trap 'kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true' EXIT
wait
