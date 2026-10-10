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
read -r MARS_BACKEND_PORT MARS_FRONTEND_PORT < <(.venv/bin/python -c 'import yaml; c=yaml.safe_load(open("configs/local_runtime.yaml")); print(c["backend_port"], c["frontend_port"])')
export BACKEND_PORT="${BACKEND_PORT:-$MARS_BACKEND_PORT}"
export FRONTEND_PORT="${FRONTEND_PORT:-$MARS_FRONTEND_PORT}"
export BACKEND_URL="http://127.0.0.1:$BACKEND_PORT"
export NEXT_PUBLIC_BACKEND_URL="$BACKEND_URL"
export NEXT_PUBLIC_WS_URL="ws://127.0.0.1:$BACKEND_PORT"
echo "[dev] starting backend on :$BACKEND_PORT"
uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --reload &
BACKEND_PID=$!

# 5. Frontend
FRONTEND_PID=""
if [ -d frontend ]; then
  cd frontend
  if [ ! -d node_modules ]; then
    echo "[dev] installing frontend deps"
    npm install --legacy-peer-deps
  fi
  echo "[dev] starting frontend on :$FRONTEND_PORT"
  npm run dev &
  FRONTEND_PID=$!
  cd "$ROOT"
fi

trap 'kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true' EXIT
wait
