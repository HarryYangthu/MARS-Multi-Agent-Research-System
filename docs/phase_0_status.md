# Phase 0 Status — Repo Scaffold

## Sub-acceptance

| Item | Status | Evidence |
|---|---|---|
| Monorepo skeleton (CLAUDE.md tree) | ✓ | `backend/app/{api,bridge,harness,agents,hitl,execution,storage,workers}` + `frontend/src/{app,components,features,lib,stores,types}` + `configs/`, `templates/`, `projects/`, `posttrain/`, `docs/`, `scripts/`, `runs/`, `workspace/`, `knowledge/{literature,methodology,code_assets,run_archive}` all created |
| `pyproject.toml` | ✓ | FastAPI / uvicorn / python-socketio / pydantic / loguru / chromadb / redis / jsonschema / python-frontmatter / langgraph / anthropic / openai + dev tools (pytest, mypy, import-linter) |
| `frontend/package.json` | ✓ | Next.js 15 / React 19 RC / TypeScript / Tailwind / socket.io-client / zustand / react-markdown / @uiw/react-md-editor / monaco / recharts |
| `docker-compose.yml` | ✓ | redis + chromadb + backend + frontend (vLLM commented out) |
| `.env.example` | ✓ | All LLM keys optional; mock fallback via `MARS_MOCK_MODE=auto` |
| `.importlinter` 4 contracts | ✓ | harness-no-upward / bridge-no-direct-agents / agents-only-via-harness / layered |
| `mypy.ini` (in pyproject) | ✓ | `strict=true`; ignore_missing for chromadb/langgraph/socketio etc. |
| `pytest.ini` (in pyproject) | ✓ | testpaths=backend/tests, asyncio_mode=auto |
| `scripts/dev.sh` | ✓ | Local launcher with venv + redis (best-effort) + uvicorn + next dev |
| `backend/app/main.py` hello world | ✓ | FastAPI app with `/health` + `/` |
| `frontend/src/app/page.tsx` empty | ✓ | "MARS V0" landing |
| `mypy --strict backend/app/` clean | ✓ | "Success: no issues found in 31 source files" |
| `lint-imports` 4/4 KEPT | ✓ | "Contracts: 4 kept, 0 broken." |
| Backend boots, `/health` returns 200 | ✓ | `{"status":"ok","service":"mars-backend","version":"0.1.0"}` on port 8765 |

## How to verify

```
cd /Users/harry/Documents/五月面试/01_MARs/mars_claude
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
mypy --strict backend/app/                    # → Success
PYTHONPATH=backend lint-imports               # → 4 kept, 0 broken
PYTHONPATH=backend uvicorn app.main:app --host 127.0.0.1 --port 8765 &
curl http://127.0.0.1:8765/health             # → {"status":"ok",...}
```

## Notes

- Local Python is 3.13.9; pyproject requires `>=3.11`. `mypy.python_version = 3.11` keeps the type-checking target conservative.
- Default port 8000 may be in use; tests use 8765. Production `docker compose` keeps 8000 internal-only via service network.
- Frontend not booted in Phase 0 (would require `npm install` + dev server). Phase 4 verifies frontend boot.
- Docker not exercised in Phase 0 (validated via `scripts/dev.sh` path); `docker compose up` will be exercised in Phase 7 acceptance.

## End-to-end checkpoint

Not applicable — no agent flow yet. CLI / Pipeline e2e begins Phase 1.
