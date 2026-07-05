# Phase 6 Status — Execution Monitor + Mock Simulation + MultiExperimentView

## Sub-acceptance

| Item | Status | Evidence |
|---|---|---|
| `execution/mock_simulation.py` (★ no-GPU fallback) | ✓ | `MockJob` / `run_mock_simulation()`; emits `execution.started` / `curve_point` / `completed` per WS event-bus channel; produces `MockResult` w/ deterministic fingerprint |
| `execution/simulation_runner.py` | ✓ | `JobSpec` + `run_one()` wraps the mock runner (real subprocess wiring is V2) |
| `execution/batch_runner.py` (concurrency cap=6) | ✓ | `BatchConfig.max_concurrency`; semaphore-bounded `asyncio.gather` |
| `execution/log_streamer.py` | ✓ | Async iterator that publishes `execution.log_line` events |
| `execution/metrics_collector.py` | ✓ | Writes per-experiment `run_log_<exp>.v1.md` (schema-valid) + `metrics.json` |
| `execution/curve_parser.py` | ✓ | Writes `runs/<id>/execution/curves/<exp>_<metric>.json` |
| Orchestrator hook | ✓ | `bridge/agent_runner.py::_run_execution_batch` runs after the Execution Agent's draft, reads upstream `experiment_plan.approved.md` for ablation names, fan-outs to `run_batch()` |
| `api/execution.py` REST endpoints | ✓ | `/metrics` / `/curves` / `/curves/{name}` / `/summary` |
| Frontend MultiExperimentView | ✓ | `frontend/src/app/runs/[id]/multi/page.tsx`: 1-6 panels, mini SVG line chart, polls `/curves` and `/summary` |
| Run-detail link to Multi view | ✓ | Sidebar "Multi view →" link |
| WS per-experiment channel isolation | ✓ | `tests/integration/test_concurrent_execution.py::test_six_jobs_run_concurrently` asserts 6 distinct channels |
| Concurrency cap honors queue | ✓ | `test_seventh_job_queues_behind_cap` (cap=2, 3 jobs → 3rd starts after 1st finishes) |
| Mock results pass schema | ✓ | `tests/unit/test_mock_simulation.py::test_result_metadata_validates_run_log_schema` |
| Fingerprint into RunArchive | ✓ | Each per-experiment run_log carries `fingerprint_hash`, sedimentation hook writes it into `run_archive` zone |
| `mypy --strict` clean | ✓ | "Success: no issues found in 99 source files" |
| `lint-imports` 4/4 KEPT | ✓ | bridge no longer imports api; bus passed via parameter |
| Pytest all green | ✓ | 209 passed |
| Frontend `next build` clean | ✓ | 6 routes generated (added `/runs/[id]/multi`) |
| HTTP e2e | ✓ | Full HITL run produces 3 per-experiment run_logs (mock plan has 3 ablations) + curves + metrics; `/api/execution/.../curves` returns 3, `/metrics` returns 3 |

## Test counts (cumulative)

```
backend/tests/                                              209 passed
   ↳ unit/test_mock_simulation.py                              3   NEW
   ↳ integration/test_concurrent_execution.py                  2   NEW
```

## How to verify

```
source .venv/bin/activate
PYTHONPATH=backend pytest backend/tests/integration/test_concurrent_execution.py -q
PYTHONPATH=backend pytest backend/tests/unit/test_mock_simulation.py -q
mypy --strict backend/app/                                # → clean
PYTHONPATH=backend lint-imports                           # → 4 kept
PYTHONPATH=backend uvicorn app.main:app --host 127.0.0.1 --port 8765 &
RID=$(curl -sX POST http://127.0.0.1:8765/api/runs \
   -H 'Content-Type: application/json' \
   -d '{"task":"phase6","project":"pimc","user_request":"test"}' \
   | python -c 'import json,sys;print(json.load(sys.stdin)["run_id"])')
curl -sX POST http://127.0.0.1:8765/api/runs/$RID/start
# approve through 5 stages…
curl -s http://127.0.0.1:8765/api/execution/$RID/metrics  # → list of N runs
curl -s http://127.0.0.1:8765/api/execution/$RID/curves   # → list of curve filenames
ls runs/$RID/execution/curves/                            # → <exp>_loss.json each
ls runs/$RID/execution/                                   # → run_log_<exp>.v1.md per ablation

# Frontend:
cd frontend && npm run dev
# http://localhost:3000/runs/<RID>/multi  → 3 mini charts
```

## End-to-end checkpoint (Phase 6)

E2E at this Phase = "Pipeline run + Execution Agent fans out N≤6 concurrent
mock simulations + per-experiment run_log + curves + metrics persisted +
fingerprints into RunArchive KB + Multi view shows mini charts".

The HTTP smoke run above demonstrates this. With the pimc mock_provider's
experiment_plan emitting 3 ablations, we see 3 simulations; the 6-way capacity
is unit-tested separately. Replacing the mock provider with real LLMs (or a
hand-written experiment_plan with 6 ablations) yields full 6-way fan-out.

## Notes / decisions

- **Concurrency cap location**: `execution/batch_runner.py::BatchConfig.max_concurrency` (default 6). The orchestrator does NOT impose its own cap on the execution sub-batch, so the unit test can independently verify `cap=2` queueing behaviour.
- **Bus passed by parameter**: orchestrator captures its bus when building the NodeRunner closure, then `run_agent_node(run, key, bus=bus)` propagates it into `_run_execution_batch`. This avoids `bridge → api` layering violation while keeping a single bus per session.
- **Curve persistence**: The WS stream publishes ticks (which the front-end can consume live), AND the runner writes a deterministic curve to disk so `/api/execution/<run>/curves/<name>` works for replay. Real GPU runs would persist actual loss values from the trainer.
- **Fingerprint into RunArchive**: handled by Phase 5 sedimentation hooks via `execution_extractor` — already in place; verified by `knowledge/run_archive/_index.json` containing the fingerprint after a run.
- **No real subprocess yet**: V0 sticks to mock simulations even when GPU is detected (see `gpu_available()` helper). Hardware E2E (V2+) will swap `simulation_runner.run_one()` to spawn the project's training subprocess.
