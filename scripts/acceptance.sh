#!/usr/bin/env bash
# MARS V0 acceptance harness — implements ACCEPTANCE.md §13.
# Each step prints a banner; failure exits non-zero.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

banner() { echo; echo "===== $* ====="; }

frontend_run() {
  if command -v pnpm >/dev/null 2>&1; then
    pnpm --dir frontend "$@"
  else
    npm --prefix frontend run "$@"
  fi
}

banner "0. activate venv (or create it)"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# Keep test defaults deterministic without forcing debate/unit tests into the
# explicit mock-only branch; the e2e demo below switches to always-mock.
export MARS_RUNTIME_MODE=development
export MARS_MOCK_MODE=auto
export MARS_EXECUTION_BACKEND=mock
export MARS_ENABLE_NETWORK_TOOLS=false
export LOCAL_VLLM_BASE_URL=
export PYTHONPATH=backend:posttrain/src

if command -v mypy >/dev/null 2>&1 && \
   command -v pytest >/dev/null 2>&1 && \
   command -v lint-imports >/dev/null 2>&1; then
  echo "Python dev dependencies already available; skipping pip install."
else
  pip install -q -e ".[dev]"
fi

banner "1. mypy --strict"
mypy --strict backend/

banner "2. import-linter"
PYTHONPATH=$PYTHONPATH lint-imports

banner "3. unit + integration tests"
PYTHONPATH=$PYTHONPATH pytest backend/tests/unit/ -q
PYTHONPATH=$PYTHONPATH pytest backend/tests/integration/ -q

banner "4. schema compliance ≥95%"
PYTHONPATH=$PYTHONPATH pytest backend/tests/schema/ -q

banner "5. gate tests"
PYTHONPATH=$PYTHONPATH pytest backend/tests/gate/ -q

banner "6. tools v1 hardening smoke"
PYTHONPATH=$PYTHONPATH pytest backend/tests/unit/test_tools_hardening.py \
  backend/tests/unit/test_search_tools_v1.py \
  backend/tests/unit/test_execution_tools_v1.py \
  backend/tests/integration/test_api_runs.py::test_tools_catalogue_endpoints_include_harness_and_bridge_tools \
  -q

banner "7. frontend typecheck + lint + context workbench smoke"
frontend_run typecheck
frontend_run lint
frontend_run test:context

banner "8. baseline matcher recall/precision"
PYTHONPATH=$PYTHONPATH pytest backend/tests/baseline/ -q

banner "9. e2e demo (zero external deps)"
export MARS_MOCK_MODE=always
export MARS_EXECUTION_BACKEND=mock
export MARS_ENABLE_NETWORK_TOOLS=false
unset ANTHROPIC_API_KEY OPENAI_API_KEY QWEN_API_KEY GEMINI_API_KEY DEEPSEEK_API_KEY
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NO_PROXY="*"
DEMO_RUN_ID_FILE="/tmp/mars-acceptance-run-id"
: > "$DEMO_RUN_ID_FILE"

PYTHONPATH=$PYTHONPATH python scripts/run_demo_inprocess.py \
  --mock-mode \
  --task "acceptance_demo" \
  --run-id-file "$DEMO_RUN_ID_FILE"

banner "10. runs/ completeness"
LATEST=$(cat "$DEMO_RUN_ID_FILE")
if [ -z "$LATEST" ]; then
  echo "  ✗ demo run_id was not recorded"
  exit 1
fi
echo "demo run: runs/$LATEST"
for sub in input context idea experiment coding execution writing hitl events; do
  if [ -d "runs/$LATEST/$sub" ] && [ -n "$(ls -A "runs/$LATEST/$sub")" ]; then
    echo "  ✓ $sub populated"
  else
    echo "  ✗ $sub MISSING/EMPTY"
    exit 1
  fi
done

banner "11. tools v1 demo audit"
PYTHONPATH=$PYTHONPATH python scripts/verify_tools_v1_acceptance.py \
  --run-id "$LATEST" \
  --in-process

banner "12. context manifest v2 coverage"
MANIFEST_COUNT=$(find "runs/$LATEST/context" -type f -name 'context_manifest.v2.*.json' | wc -l | tr -d ' ')
echo "context v2 manifests: $MANIFEST_COUNT"
if [ "$MANIFEST_COUNT" -lt 5 ]; then
  echo "  ✗ expected at least 5 pre-call context_manifest.v2 files"
  exit 1
fi
if [ ! -f "runs/$LATEST/context/context_manifest.v2.json" ]; then
  echo "  ✗ context_manifest.v2 index missing"
  exit 1
fi
CONTEXT_SUMMARY_FILE="/tmp/mars-acceptance-context-summary.json"
PYTHONPATH=$PYTHONPATH python - "$LATEST" "$CONTEXT_SUMMARY_FILE" <<'PY'
import json
import sys

from fastapi.testclient import TestClient

from app.main import create_app

run_id = sys.argv[1]
path = sys.argv[2]
client = TestClient(create_app())
response = client.get(f"/api/context/runs/{run_id}")
if response.status_code != 200:
    raise SystemExit(f"context workbench API failed: {response.status_code} {response.text}")
with open(path, "w", encoding="utf-8") as handle:
    json.dump(response.json(), handle)
with open(path, "r", encoding="utf-8") as handle:
    data = json.load(handle)
manifest_count = data.get("budget_summary", {}).get("manifest_count", 0)
if manifest_count < 5:
    raise SystemExit(f"context workbench API returned only {manifest_count} manifests")
if not data.get("manifests"):
    raise SystemExit("context workbench API returned no manifest summaries")
print(f"  ✓ context workbench API manifests: {manifest_count}")
PY

banner "12b. context workbench acceptance checkpoint passed"

banner "13. posttrain CPU/mock dry-run"
POSTTRAIN_REPORT_FILE="/tmp/mars-acceptance-posttrain-report.json"
PYTHONPATH=$PYTHONPATH python -m mars_posttrain \
  --run-root "runs/$LATEST" \
  --output-root posttrain \
  --include-drafts \
  > "$POSTTRAIN_REPORT_FILE"
PYTHONPATH=$PYTHONPATH python - "$POSTTRAIN_REPORT_FILE" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    data = json.load(handle)
if data.get("schema") != "posttrain_dry_run_report.v1":
    raise SystemExit("posttrain dry-run report schema mismatch")
if data.get("eligible_count", 0) < 1:
    raise SystemExit("posttrain dry-run found no eligible records")
if data.get("preference_pair_count", 0) < 1:
    raise SystemExit("posttrain dry-run constructed no preference pairs")
acceptance = data.get("acceptance", {})
required_rewards = {"schema_validity", "baseline_preservation", "downstream_metric"}
if set(acceptance.get("reward_families", [])) != required_rewards:
    raise SystemExit("posttrain dry-run reward families are incomplete")
if acceptance.get("requires_gpu") is not False:
    raise SystemExit("posttrain dry-run must not require GPU")
print(f"  ✓ posttrain dry-run report: {data.get('report_path')}")
print(f"  ✓ posttrain mock checkpoint: {data.get('checkpoint_path')}")
PY

banner "✅ V0 + Tools V1 + Context Workbench + Posttrain dry-run acceptance passed"
