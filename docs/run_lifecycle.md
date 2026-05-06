# A run from create to archive — the timeline

> Sequence-diagram-style. Anchored to ACCEPTANCE §2 11-step main script + `scripts/run_demo.py`.

## Actors

- `User` (human or `scripts/run_demo.py`)
- `API` (FastAPI; `api/runs.py`, `api/artifacts.py`)
- `Bridge` (`bridge/orchestrator.py`)
- `Agent` (any of Idea/Experiment/Coding/Execution/Writing — looked up via `bridge/agent_registry`)
- `LLM` (real provider or `mock_provider`)
- `Storage` (`storage/run_store`, `storage/artifact_store`)
- `KB` (`harness/kb/*`)
- `Bus` (`harness/runtime/event_bus`)
- `HITL` (`hitl/review_session`)
- `Gate5` (`harness/gates/baseline_compatibility` via `harness/tools/registry.dispatch`)

## Phase A — create + start

```
User  ─POST /api/runs────────►  API
                                 │
                                 ▼
                            Bridge.create_session()
                                 │ ▼
                                 │  Storage.RunStore.create() → runs/<ts>_<task>/
                                 │  └ 9 subdirs + run_meta.json + input/user_request.md
                                 │
                                 ▼
                            workflow_service.build_pipeline(entrypoint)
                                 ▼
                            RunGraph (5 nodes, all PENDING)
                                 ▼
                            return RunDetail to API → User
```

```
User  ─POST /api/runs/<id>/start───►  API
                                       │
                                       ▼
                                 asyncio.create_task(orchestrator.run(id))
```

## Phase B — per-Agent loop

For each ready node `agent_name`:

```
Bridge.transition(agent, RUNNING)              ──►  Bus.publish("run.<id>.agent_state", ...)
       ↓
Bridge.run_agent_node(run, agent_name, bus)
       │
       │  read input/user_request.md  +  upstream/*.approved.md
       │
       ▼
Agent.build_context(request)            (3-layer ContextPack — system + project + task)
       ▼
Agent.draft(request, context)
       │
       │  if debate_enabled:
       │     run_debate() — 3 modes auto-selected (real / single-sim / mock)
       │  else:
       │     LLM.complete()
       │
       │  for tool calls inside the agent:
       │      ToolRegistry.dispatch(tool, args, ctx)
       │              ▲
       │              │ Gate5.gate_check(tool, args, ctx)  ←  reads project/AGENTS.md + repo_link.yaml
       │              │   if path/forward_signature violation:
       │              │     return BLOCKED, ToolResult(blocked_by_gate="baseline_compatibility")
       │              ▼
       │      tool runs, returns
       │
       ▼
Agent.validate_output(artifact)         (frontmatter + JSON Schema)
       ▼
Storage.ArtifactStore.write()           (writes runs/<id>/<agent>/<stem>.vN.md)
       ▼
Manifest.write(run_root, pack, agent)   (writes runs/<id>/context/<agent>_context_pack.vN.json)
       ▼
Sedimentation.on_agent_completed()      (writes to KB zones — literature/methodology/code_assets/run_archive)
       ▼
Bridge.transition(agent, WAITING_REVIEW)──►  Bus.publish("run.<id>.hitl.review_required")
```

## Phase C — HITL decision

```
User  ─POST /api/artifacts/<id>/<agent>/<stem>/v1/approve───►  API.artifacts
                                                                 │
                                                                 ▼
                                                            HITL.approve(session, bus)
                                                                 ▼
                                                            ArtifactStore.approve(ref)  → vN → approved.md
                                                                 ▼
                                                            session.approval_event.set()
                                                                 ▼
                                                            Bridge polling loop wakes:
                                                            transition(agent, APPROVED) → DONE
                                                                 ▼
                                                            Bus.publish("run.<id>.agent_state", DONE)
```

(Same shape for `reject` → `FAILED`. The orchestrator stops advancing downstream when any node reaches FAILED.)

## Phase D — Execution Agent (additional)

After the Execution Agent's draft + auto/HITL approve:

```
agent_runner._run_execution_batch(run, bus)
       │
       │  reads upstream experiment_plan.approved.md, extracts ablation names
       │
       ▼
batch_runner.run_batch(specs, BatchConfig(max_concurrency=6), bus_publish=pub)
       │
       │  per-experiment, in parallel up to cap=6:
       │      mock_simulation.run_mock_simulation(job, bus_publish=pub)
       │              │
       │              ├──► Bus.publish("run.<id>.experiment.<exp>", "execution.started")
       │              ├──► tick × N: "execution.curve_point" with step + value
       │              └──► "execution.completed" + fingerprint_hash
       │
       ▼
metrics_collector.write_run_log()       per ablation → runs/<id>/execution/run_log_<exp>.v1.md
curve_parser.write_curve()              per ablation → runs/<id>/execution/curves/<exp>_loss.json
metrics_collector.write_metrics_json()  → runs/<id>/execution/metrics.json
batch_summary.json                      → runs/<id>/execution/batch_summary.json
```

## Phase E — completion

When every node reaches DONE (or SKIPPED):

```
Bridge._publish_state(channel="run.lifecycle", payload={event: "run.completed"})
```

`runs/<id>/` now contains:

```
input/        ← user_request.md
context/      ← <agent>_context_pack.v1.json + <agent>_context_snapshot.v1.md  (×5)
idea/         ← idea_proposal.{v1, approved}.md
experiment/   ← experiment_plan.{v1, approved}.md
coding/       ← code_spec.{v1, approved}.md
execution/    ← run_log.{v1, approved}.md + run_log_<exp>.v1.md per ablation + curves/ + metrics.json + batch_summary.json
writing/      ← research_report.{v1, approved}.md
hitl/         ← review_log.jsonl
events/       ← agent_events.jsonl + websocket_events.jsonl
```

The `runs/<id>/` directory is the **single source of truth** for replay, audit, and post-training data construction.
