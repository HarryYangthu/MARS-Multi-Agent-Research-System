#!/usr/bin/env bash
# MARS V0 acceptance harness — implements ACCEPTANCE.md §13.
# Each step prints a banner; failure exits non-zero.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

banner() { echo; echo "===== $* ====="; }

banner "0. activate venv (or create it)"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -e ".[dev]"

banner "1. mypy --strict"
mypy --strict backend/

banner "2. import-linter"
PYTHONPATH=backend lint-imports

banner "3. unit + integration tests"
PYTHONPATH=backend pytest backend/tests/unit/ -q
PYTHONPATH=backend pytest backend/tests/integration/ -q

banner "4. schema compliance ≥95%"
PYTHONPATH=backend pytest backend/tests/schema/ -q

banner "5. gate tests"
PYTHONPATH=backend pytest backend/tests/gate/ -q

banner "6. baseline matcher recall/precision"
PYTHONPATH=backend pytest backend/tests/baseline/ -q

banner "7. e2e demo (zero external deps)"
# Spawn backend on a free port (don't clash with system services on 8000).
PORT=8765
PYTHONPATH=backend uvicorn app.main:app \
  --host 127.0.0.1 --port "$PORT" \
  --log-level warning > /tmp/mars-acceptance.log 2>&1 &
BACKEND_PID=$!
trap 'kill $BACKEND_PID 2>/dev/null || true' EXIT

# wait for /health
for i in $(seq 1 20); do
  if curl -sf "http://127.0.0.1:$PORT/health" > /dev/null; then
    break
  fi
  sleep 0.5
done

PYTHONPATH=backend python scripts/run_demo.py --port "$PORT" --mock-mode --task "acceptance_demo"

banner "8. runs/ completeness"
LATEST=$(ls -1t runs/ | head -n 1)
echo "latest run: runs/$LATEST"
for sub in input context idea experiment coding execution writing hitl events; do
  if [ -d "runs/$LATEST/$sub" ] && [ -n "$(ls -A "runs/$LATEST/$sub")" ]; then
    echo "  ✓ $sub populated"
  else
    echo "  ✗ $sub MISSING/EMPTY"
    exit 1
  fi
done

banner "✅ V0 acceptance passed"
