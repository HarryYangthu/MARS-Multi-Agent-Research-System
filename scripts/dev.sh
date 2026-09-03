#!/usr/bin/env bash
# Native V3.1 development launcher for macOS/Linux. Docker and Redis are not used.
set -euo pipefail

MARS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARS_PARENT="$(dirname "$MARS_ROOT")"
MARS_OVERLAY="${MARS_V31_OVERLAY_PATH:-$MARS_PARENT/mars_v31_wireless}"
MARS_BACKEND_PORT="${BACKEND_PORT:-8000}"
MARS_FRONTEND_PORT="${FRONTEND_PORT:-3001}"
MARS_PYTHON_BIN="${MARS_PYTHON:-}"

if [ -z "$MARS_PYTHON_BIN" ]; then
  for candidate in python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && \
       "$candidate" -c 'import sys; assert sys.version_info[:2] == (3, 11)' 2>/dev/null; then
      MARS_PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  done
fi

if [ -z "$MARS_PYTHON_BIN" ] || \
   ! "$MARS_PYTHON_BIN" -c 'import sys; assert sys.version_info[:2] == (3, 11)' 2>/dev/null; then
  echo "[mars] Python 3.11 is required. Current interpreter: $MARS_PYTHON_BIN" >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "[mars] Node.js 20+ and npm are required." >&2
  exit 1
fi
MARS_NODE_MAJOR="$(node -p "process.versions.node.split('.')[0]")"
if [ "$MARS_NODE_MAJOR" -lt 20 ]; then
  echo "[mars] Node.js 20+ is required. Current major version: $MARS_NODE_MAJOR" >&2
  exit 1
fi

if [ ! -f "$MARS_OVERLAY/project_packs/pimc/project_pack.yaml" ] || \
   [ ! -f "$MARS_OVERLAY/src/mars_v31_wireless/adapter.py" ]; then
  cat >&2 <<EOF
[mars] The V3.1 Wireless Overlay was not found at:
  $MARS_OVERLAY

Place mars_v31_wireless beside this repository, or set:
  MARS_V31_OVERLAY_PATH=/absolute/path/to/mars_v31_wireless
EOF
  exit 1
fi

cd "$MARS_ROOT"
if [ ! -f .env ] && [ ! -f .env.local ]; then
  echo "[mars] No local env file found; using safe mock/CPU defaults."
fi

if [ ! -d .venv ]; then
  echo "[mars] Creating Python virtual environment..."
  "$MARS_PYTHON_BIN" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
if [ "${MARS_SKIP_INSTALL:-0}" != "1" ]; then
  MARS_OVERLAY_INSTALL="$MARS_OVERLAY"
  if [ "${MARS_INSTALL_STATIC_CPU:-0}" = "1" ]; then
    MARS_OVERLAY_INSTALL="$MARS_OVERLAY[static]"
    echo "[mars] Static CPU dependencies are enabled; the first install may be large."
  fi
  echo "[mars] Installing/updating Core, V3.1 Overlay, and synthetic adapter dependencies..."
  python -m pip install --disable-pip-version-check -q \
    ".[dev]" \
    "$MARS_OVERLAY_INSTALL" \
    "$MARS_ROOT/projects/synthetic_regression"
fi

if [ ! -d frontend/node_modules ]; then
  echo "[mars] Installing frontend dependencies..."
  (cd frontend && npm ci --legacy-peer-deps)
fi

export PYTHONPATH="$MARS_ROOT/backend:$MARS_OVERLAY/src:$MARS_ROOT/projects/synthetic_regression/src"
export MARS_DISTRIBUTION="v31-wireless"
export MARS_PROJECT_PACK_PATHS="$MARS_OVERLAY/project_packs"
export MARS_EXECUTION_DEVICE="${MARS_EXECUTION_DEVICE:-cpu}"
export MARS_PAPER_STATIC_PYTHON="$MARS_ROOT/.venv/bin/python"
export REDIS_URL=""
export BACKEND_HOST="127.0.0.1"
export BACKEND_PORT="$MARS_BACKEND_PORT"
export FRONTEND_PORT="$MARS_FRONTEND_PORT"
export BACKEND_URL="http://127.0.0.1:$MARS_BACKEND_PORT"
export NEXT_PUBLIC_BACKEND_URL="$BACKEND_URL"
export NEXT_PUBLIC_WS_URL="ws://127.0.0.1:$MARS_BACKEND_PORT"
export MARS_CORS_ORIGINS="http://127.0.0.1:$MARS_FRONTEND_PORT,http://localhost:$MARS_FRONTEND_PORT"
export NEXT_TELEMETRY_DISABLED="1"

MARS_PIDS=()
cleanup() {
  trap - EXIT INT TERM
  for pid in "${MARS_PIDS[@]:-}"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[mars] Starting V3.1 backend on http://127.0.0.1:$MARS_BACKEND_PORT"
python -m uvicorn app.main:app --host 127.0.0.1 --port "$MARS_BACKEND_PORT" \
  --reload --reload-dir "$MARS_ROOT/backend" --reload-dir "$MARS_OVERLAY/src" &
MARS_PIDS+=("$!")

echo "[mars] Starting V3.1 frontend on http://127.0.0.1:$MARS_FRONTEND_PORT"
(
  cd frontend
  npm run dev -- --hostname 127.0.0.1 --port "$MARS_FRONTEND_PORT"
) &
MARS_PIDS+=("$!")

echo "[mars] Native development mode is running. Press Ctrl+C to stop both services."
while kill -0 "${MARS_PIDS[0]}" 2>/dev/null && kill -0 "${MARS_PIDS[1]}" 2>/dev/null; do
  sleep 1
done

echo "[mars] A service exited unexpectedly; stopping the remaining process." >&2
exit 1
