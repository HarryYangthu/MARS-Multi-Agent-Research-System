# MARS V0 — Architecture

> Companion to `DESIGN.md`. This file shows what's actually wired and where to start reading.

## 1. Layered overview (5 Tier)

```
┌────────────────────────────────────────────────────────────────┐
│ Tier 1  Web Workbench (Next.js 15)                             │
│   • Dashboard / NewRun / RunDetail / MultiExperimentView       │
│   • Markdown editor + diff + version timeline + HITL actions   │
└────────────────────────────────────────────────────────────────┘
                          ▲ HTTP / WS
┌────────────────────────────────────────────────────────────────┐
│ Tier 2  API + Bridge (FastAPI)                                 │
│   • api/runs.py · api/artifacts.py · api/execution.py · ws     │
│   • bridge/orchestrator.py drives the RunGraph                  │
│   • bridge/agent_registry.py reverses agent dependency          │
│   • bridge/workflow_service.py owns the linear topology         │
│   • bridge/agent_runner.py adapts a registered agent → NodeRun  │
└────────────────────────────────────────────────────────────────┘
                          ▲ in-process calls
┌────────────────────────────────────────────────────────────────┐
│ Tier 3  Five Agents                                            │
│   • IdeaAgent (debate-on)  → proposal.v1                        │
│   • ExperimentAgent        → experiment_plan.v1                 │
│   • CodingAgent            → code_spec.v1                       │
│   • ExecutionAgent         → run_log.v1   (+ batch sims)        │
│   • WritingAgent (debate-on) → report.v1                        │
│   • debate/ runner with 3 modes (real / single-sim / mock)      │
└────────────────────────────────────────────────────────────────┘
                          ▲ ABCs + Protocols
┌────────────────────────────────────────────────────────────────┐
│ Tier 4  Harness Services (agent-agnostic)                      │
│   • runtime/ · run_graph + state_machine + queue + event_bus   │
│   • schema/  · 5 JSON Schemas + frontmatter parser + validator │
│   • llm/     · 6 providers + mock + post_training_loader        │
│   • context/ · 3-layer loader + Manifest + compressor           │
│   • kb/      · 4-zone store + embedder + matcher + fingerprint  │
│   • gates/   · 5 gates (Gate 5 hooks tools/registry.dispatch)   │
│   • tools/   · registry with gate-checked dispatch              │
│   • sedimentation/ · per-Agent extractors + hooks               │
└────────────────────────────────────────────────────────────────┘
                          ▲ filesystem
┌────────────────────────────────────────────────────────────────┐
│ Tier 5  Storage & Projects                                     │
│   • storage/ run_store + artifact_store + file_store           │
│   • runs/<timestamp>_<task>/ (9 subdirs)                        │
│   • knowledge/<zone>/_index.json (4 KB zones)                   │
│   • workspace/repos/<project>/ (research code via repo_link)    │
│   • projects/<name>/ AGENTS.md + repo_link.yaml + data_gen.py   │
└────────────────────────────────────────────────────────────────┘
```

## 2. Dependency direction (CI-enforced via .importlinter)

```
api  →  bridge  →  hitl  →  (agents | execution | workers)  →  storage  →  harness
```

Four .importlinter contracts:

1. **harness-no-upward** — harness/ may not import bridge/ or agents/ or hitl/ or api/ or execution/ or workers/
2. **bridge-no-direct-agents** — bridge/ may not import any of `app.agents.idea`, `app.agents.experiment`, `app.agents.coding`, `app.agents.execution`, `app.agents.writing` (must go via `bridge/agent_registry`)
3. **agents-only-via-harness** — agents/ may not import bridge/ or api/
4. **layered architecture** — api > bridge > hitl > (agents | execution | workers) > storage > harness

CI fails if any contract is broken.

## 3. Schema is the spine

```
LLM (real or mock_provider)
    ↓ markdown body + YAML frontmatter
frontmatter_parser.py
    ↓
validator.py (against schemas/<schema>.v1.json)
    ↓ valid → ArtifactStore.write()
runs/<id>/<agent>/<stem>.v<N>.md
    ↓ HITL approve (or auto_approve flag)
runs/<id>/<agent>/<stem>.approved.md
```

The **same** path is used whether the artifact came from an Agent or from a human. There is no "agent-only" or "human-only" variant.

## 4. Two kinds of HITL

| Layer | Module | Frequency | Trigger |
|---|---|---|---|
| Per-Agent review | `hitl/review_session.py` | high (every node) | Orchestrator parks at `WAITING_REVIEW` |
| 5 system Gates  | `harness/gates/*.py` | sparse | Gate 1/2/3/4 by RunGraph hooks; **Gate 5 by tool dispatch** |

Gate 5 is special — it's not on the flow. It's hooked into `harness/tools/registry.py::dispatch()`. Every tool call goes through it; if a project's `AGENTS.md` rule fires, the tool returns blocked-by-gate without running.

## 5. The 4 KB zones

| Internal | UI label (Chinese) | Population path |
|---|---|---|
| `literature` | 文献库 | Idea Agent → `idea_extractor` |
| `methodology` | 方法库 | Idea / Experiment / Writing extractors |
| `code_assets` | 代码资产库 | Coding Agent extractor |
| `run_archive` | 实验运行档案 | Execution Agent extractor (with fingerprint) |

Each zone is a JSON-persisted vector store with a deterministic 256-d hash embedder (V0). Hardware E2E can swap to ChromaDB by extending `harness/kb/stores.py`.

## 6. Mock fallback ladder

| Component | When mocks engage | Module |
|---|---|---|
| LLM | API key absent for the provider | `harness/llm/mock_provider.py` |
| Debate | required providers missing | `agents/debate/debate_runner.py::_auto_mode` |
| Simulation | always in V0 (real subprocess is V1) | `execution/mock_simulation.py` |

Together they let V0 boot on a laptop with zero API keys, zero GPUs, zero network — and still run the entire 11-step demo (`scripts/run_demo.py`).

## 7. Where to start reading

1. `CLAUDE.md` — hard constraints
2. `PRODUCT.md` — what each Agent does
3. `DESIGN.md` — architectural patterns
4. `ACCEPTANCE.md` — what counts as done
5. `backend/app/main.py` — runtime wiring
6. `backend/app/bridge/orchestrator.py` — the RunGraph driver
7. `scripts/run_demo.py` — concrete 11-step e2e

## 8. Diagram-as-tree of the codebase

```
mars_claude/
├─ frontend/                          (Next.js 15)
├─ backend/app/
│  ├─ main.py                         (FastAPI entry, agent registration)
│  ├─ settings.py                     (.env loader)
│  ├─ api/                            (REST + WS)
│  ├─ bridge/                         (orchestrator, agent_registry, workflow_service)
│  ├─ hitl/                           (review_session, audit, diff, approval)
│  ├─ agents/                         (5 agents + debate runner)
│  ├─ execution/                      (mock_simulation, batch, curve, metrics)
│  ├─ harness/                        (the agent-agnostic substrate)
│  │  ├─ runtime/  schema/  llm/  context/  kb/  gates/  tools/  sedimentation/
│  ├─ storage/                        (run_store, artifact_store, file_store)
│  └─ workers/                        (placeholders — V0 uses asyncio inline)
├─ configs/                           (agents/models/tools/gates/knowledge/execution.yaml)
├─ projects/moe-pimc/                 (project metadata + AGENTS.md + data_gen)
├─ workspace/repos/pimc-stub/         (Dev-E2E placeholder for the real research repo)
├─ knowledge/                         (4 KB zones — JSON-persisted)
├─ runs/                              (per-task sedimentation: 9 subdirs each)
├─ templates/                         (artifact + code_rules templates)
├─ scripts/                           (dev.sh, cli_validate, run_demo, acceptance.sh)
└─ docs/                              (this file + per-Phase status + run_lifecycle etc.)
```
