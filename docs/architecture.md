# MARS Architecture

> 当前代码架构速览。详细产品语义见 `PRODUCT.md`,详细模块设计见 `DESIGN.md`。

## 1. One-screen Overview

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Frontend: Next.js workbench                                         │
│ Lab / RunDetail / Context Workbench / Commander Chat / Ops panels   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ HTTP / WebSocket
┌───────────────────────────────▼─────────────────────────────────────┐
│ API: FastAPI routers                                                │
│ runs / artifacts / agents / chat / tools / context / traces / ws     │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│ Bridge: product orchestration                                       │
│ Commander · Orchestrator · WorkflowService · AgentRegistry           │
│ Diagnosis · EvaluationService · Feedback loop                        │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ registry lookup
┌───────────────────────────────▼─────────────────────────────────────┐
│ Agents: domain work                                                 │
│ Idea · Experiment · Coding · Execution · Writing · Debate            │
│ BaseAgent loop: context -> tools -> draft -> schema repair           │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ uses
┌───────────────────────────────▼─────────────────────────────────────┐
│ Harness: agent-agnostic governance                                  │
│ runtime · schema · tools · gates · context · llm · kb · evaluation   │
│ observability · sedimentation                                        │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ files + configured backends
┌───────────────────────────────▼─────────────────────────────────────┐
│ Storage / Projects                                                  │
│ runs/<id>/ · knowledge/ · configs/agent_contexts/ · repo_link.yaml   │
└─────────────────────────────────────────────────────────────────────┘
```

## 2. Source Of Truth

The source of truth is the run directory:

```text
runs/<run_id>/
├─ run_meta.json
├─ input/
├─ context/
├─ idea/
├─ experiment/
├─ coding/
├─ execution/
├─ writing/
├─ hitl/
├─ events/
└─ memory/        # created when feedback/self-evolution writes learning data
```

Live WebSocket views are projections. After refresh, the UI should be recoverable from REST endpoints plus files under `runs/<run_id>/`.

## 3. Dependency Rules

Important boundaries:

1. `harness/` does not import `bridge/`, `agents/`, `api/`, or frontend code.
2. `bridge/` uses `bridge/agent_registry.py`; it does not directly import concrete Agent classes in orchestration code.
3. `harness/runtime/run_graph.py` is a generic DAG and does not know the five-stage topology.
4. Agent actions go through `harness/tools/registry.py`.
5. Project research code is linked by `projects/<name>/repo_link.yaml`, not copied into this repository.

## 4. Runtime Flow

```text
POST /api/runs or Commander run.create
  -> RunStore.create()
  -> workflow_service.build_pipeline(entrypoint)
  -> Orchestrator.run()
  -> ready node RUNNING
  -> Agent.build_context()
  -> BaseAgent.run_loop()
       -> optional tool gather through ToolRegistry
       -> draft
       -> schema validation
       -> schema repair if needed
  -> ArtifactStore.write(vN)
  -> EvaluationRunner writes *.eval.md
  -> Sedimentation writes KB/MemoryRecord
  -> WAITING_REVIEW
  -> HITL approve
  -> DONE
  -> next node
  -> evaluation_scorecard.json on run completion
```

Execution has one extra phase: after the Execution artifact is approved, the bridge invokes `execution.batch_runner` through ToolRegistry so batch simulations get the same audit, trace, and policy coverage as other tools.

## 5. Tool Dispatch Boundary

All tools share this dispatch sequence:

```text
tool.started event
  -> exists?
  -> enabled in configs/tools.yaml?
  -> allowed for agent?
  -> input_schema valid?
  -> approval required?
  -> Gate hooks, including Gate 5 baseline compatibility
  -> run tool with timeout
  -> tool_events.jsonl + tool_calls.jsonl + trace span
```

Gate 5 lives here, not in the pipeline graph. A baseline-breaking patch is blocked before the write tool runs.

## 6. Context / Memory / Evaluation

Context:

- `harness/context/engine.py` compiles context into segments.
- `context_manifest.v2.*.json` records budget, render order, message preview, diagnostics, and raw refs.
- Large tool outputs go to `context/raw/`.

Memory:

- KB zones: `literature`, `methodology`, `code_assets`, `run_archive`.
- `MemoryRecord` v2 tracks type, source, confidence, salience, TTL, eval status, approval, supersession, and mock status.
- Pending memory candidates do not enter future prompts until approved.

Evaluation:

- `evaluation_report.v1` is the normalized output.
- Current default evaluators: schema validity, provenance, artifact quality rubric.
- Run completion writes `events/evaluation_scorecard.json`.
- Post-training export writes candidates only; training remains out of scope here.

## 7. Observability

Durable evidence:

```text
events/*.jsonl
context/trace_manifest.v2.json
context/context_manifest.v2.*.json
execution/metrics.json
execution/logs/
hitl/review_log.jsonl
events/evaluation_scorecard.json
```

Live evidence:

- EventBus -> WebSocket run channels.
- Per-experiment execution channels.
- Optional LangSmith sink, disabled by default.

The default file-backed path must remain sufficient for replay and debugging.

## 8. Key Files

```text
backend/app/main.py                         App/router/Agent registration
backend/app/bridge/commander.py             Conversational control plane
backend/app/bridge/orchestrator.py          RunGraph driver
backend/app/bridge/workflow_service.py      Pipeline/standalone topology
backend/app/agents/base.py                  Agent loop
backend/app/harness/tools/registry.py       Tool policy, Gate 5, audit
backend/app/harness/context/engine.py       Context V2 compiler
backend/app/harness/evaluation/             Eval reports and scorecards
backend/app/harness/kb/models.py            MemoryRecord v2
backend/app/storage/self_evolution_store.py Manual-review self-evolution
backend/app/harness/observability/          Events, traces, LangSmith mirror
backend/app/execution/                      Mock/local execution runners
configs/agents.yaml                         Agent/model/tool config
configs/tools.yaml                          Tool control plane
configs/memory.yaml                         Memory governance
configs/evaluation.yaml                     Evaluation policy
configs/observability.yaml                  Observability sinks
```

## 9. Reading Order

1. `PRODUCT.md`: what the product is.
2. `DESIGN.md`: current implementation design.
3. `docs/run_lifecycle.md`: run timeline.
4. `docs/tools_catalog.md`: tool list and audit contract.
5. `docs/evaluation_system.md`: evaluation model.
6. `docs/observability_design.md`: event/trace model.
7. `docs/tool_security.md`: dispatch, rollback, network policy.
