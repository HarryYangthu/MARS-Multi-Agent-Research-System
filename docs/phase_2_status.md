# Phase 2 Status — Bridge + RunGraph + state machine + event bus + WS

## Sub-acceptance

| Item | Status | Evidence |
|---|---|---|
| `harness/runtime/run_graph.py` (generic DAG) | ✓ | `RunGraph` with topo sort + `ready_nodes()` + `is_complete()`; **no linear topology hard-coded** |
| `harness/runtime/state_machine.py` (7 states) | ✓ | pending / running / waiting_review / approved / failed / done / skipped + transition table |
| `harness/runtime/event_bus.py` | ✓ | `InProcessEventBus` (Dev E2E default) + `RedisEventBus` (auto-fallback) + `build_event_bus()` factory |
| `harness/runtime/queue_manager.py` | ✓ | asyncio bounded semaphore, `max_concurrency=6` (matches Phase 6 cap) |
| `bridge/agent_registry.py` (reverse dep) | ✓ | `Protocol`-based registry; agents register via `register("idea", ...)` |
| `bridge/workflow_service.py` (linear topology lives HERE) | ✓ | `LINEAR_STAGES = (idea, experiment, coding, execution, writing)`; `build_pipeline()` + `build_standalone()`; entrypoint pre-skips upstream |
| `bridge/orchestrator.py` | ✓ | Drives ready→running→waiting_review→approved→done loop, publishes WS events, writes `runs/<id>/events/{agent,websocket}_events.jsonl` |
| `bridge/project_isolation.py` | ✓ | Single-project placeholder (defaults to `pimc` from `.env`) |
| `api/runs.py` REST | ✓ | POST / GET / GET-by-id / start / stop |
| `api/websocket.py` | ✓ | `/ws/runs/{run_id}` (lifecycle + agent_state) and `/ws/runs/{run_id}/experiment/{exp_id}` (Phase 6 hook) |
| `bridge/` no direct agent imports | ✓ | `tests/integration/test_bridge_no_agent_import.py` AST-walks bridge/ |
| 4 import-linter contracts KEPT | ✓ | "Contracts: 4 kept, 0 broken." |
| `mypy --strict` clean | ✓ | "Success: no issues found in 47 source files" |
| Pytest all green | ✓ | 153 passed (incl. 9 new for Phase 2) |
| End-to-end via API | ✓ | `curl POST /api/runs` → `start` → `GET` shows all 5 nodes `done`; `runs/<id>/events/agent_events.jsonl` has 20 transition lines |

## Test counts (cumulative)

```
backend/tests/schema/                            ≈ 100
backend/tests/unit/test_artifact_store.py             4
backend/tests/unit/test_event_bus.py                  3
backend/tests/unit/test_frontmatter_parser.py         4
backend/tests/unit/test_run_graph.py                  8
backend/tests/unit/test_run_store.py                  3
backend/tests/unit/test_state_machine.py              5
backend/tests/unit/test_validator.py                  4
backend/tests/unit/test_workflow_service.py           4
backend/tests/integration/test_api_runs.py            3
backend/tests/integration/test_bridge_no_agent_import.py  1
backend/tests/integration/test_orchestrator_dummy_run.py  2
total: 153 passed
```

## How to verify

```
source .venv/bin/activate
mypy --strict backend/app/                # → clean
PYTHONPATH=backend lint-imports           # → 4 kept, 0 broken
PYTHONPATH=backend pytest backend/tests/  # → 153 passed
PYTHONPATH=backend uvicorn app.main:app --host 127.0.0.1 --port 8765 &
curl -s -XPOST http://127.0.0.1:8765/api/runs \
    -H 'Content-Type: application/json' \
    -d '{"task":"smoke","project":"pimc","entrypoint":"pipeline"}'
# returns run_id; then POST /api/runs/<run_id>/start; then GET shows states={...:done}
```

## End-to-end checkpoint (Phase 2)

E2E at this Phase = "API → orchestrator walks 5-node DAG → state events flow → events jsonl persisted".
The above curl sequence demonstrates this. Real Agents (Phase 3) plug into the same orchestrator via `agent_registry`. Pipeline is **already** end-to-end runnable, just with stub node runners.

## Notes / decisions

- **Reverse dependency**: `bridge/orchestrator.py` accepts a `RunnableAgent` Protocol (structural typing), so it never imports `app.agents.*`. Agents will self-register at app startup in Phase 3.
- **Default node runner**: when no agent is registered for a node, the orchestrator runs a no-op stub that still drives the state machine through running→waiting_review→approved→done. This is what makes Phase 2 e2e-runnable on its own and is replaced node-by-node in later Phases.
- **Auto-approve in Phase 2**: in this Phase the orchestrator self-approves the `WAITING_REVIEW` state. Phase 4 wires `WAITING_REVIEW` to a real human-driven `hitl/review_session.py` and removes the auto-approve.
- **Event bus**: `InProcessEventBus` is the V0 default. `RedisEventBus` is implemented but only used when `REDIS_URL` is reachable; the factory probes and silently falls back. This keeps Dev E2E fully zero-dependency.
- **Schema-pillar reminder**: orchestrator does not yet write Agent artifacts; Phase 3 wires `BaseAgent` and validation+`artifact_store.write()` happens there.
