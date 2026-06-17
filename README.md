# MARS · Multi-Agent Research System

> An opinionated substrate for **research-grade multi-agent pipelines** —
> turn a research question into a paper draft via 5 specialized LLM agents,
> with strict schema validation, human-in-the-loop review at every step,
> and full audit-replay sedimentation.

[简体中文](README.zh-CN.md) · English

![status](https://img.shields.io/badge/V0-passing-brightgreen) ![python](https://img.shields.io/badge/python-3.11%2B-blue) ![next](https://img.shields.io/badge/next.js-15-black) ![license](https://img.shields.io/badge/license-MIT-lightgrey)

---

## What is MARS?

MARS is a workbench that compresses the research loop from **months to weeks**.
A researcher's question flows through five composable agents:

```
Idea  →  Experiment  →  Coding  →  Execution  →  Writing
```

Every transition is gated by a **schema-validated Markdown artifact** and a
**human review checkpoint**. The harness underneath provides multi-model
debate, a 4-zone knowledge base, baseline-reuse fingerprinting, and a tool
dispatcher whose **Gate 5** statically rejects any patch that would break
the project's protected baselines.

The first concrete project living on top of MARS is `projects/moe-pimc/` —
*PIMC for FDD Massive MIMO under beam/layer switching*.

## Highlights

- **5 specialized agents** (Idea / Experiment / Coding / Execution / Writing),
  each with its own LLM, tools, and (optional) multi-role debate.
- **Schema is the spine.** Every artifact is `markdown body + YAML frontmatter`,
  validated against one of 5 JSON Schemas. Human-written and agent-written
  documents are interchangeable downstream.
- **HITL at every step.** Each agent parks at `WAITING_REVIEW`; the next
  agent only starts after you click Approve. Reject halts the chain.
- **5 system gates** — flow gates 1-4 plus **Gate 5 hooked into the tool
  dispatch path**, blocking any patch that mutates a baseline-protected
  surface based on the project's `AGENTS.md` static rules.
- **Multi-model debate.** Idea / Writing agents run a 3-role debate by
  default; mode auto-degrades from `real_multi_model` →
  `single_model_simulated` → `mock_debate` based on which API keys are
  present.
- **Provider-agnostic LLM layer.** First-class support for Anthropic,
  OpenAI, Qwen, Gemini, **DeepSeek**, local vLLM, and an OpenAI-compatible
  custom endpoint.
- **4-zone shared knowledge base** (literature / methodology / code_assets
  / run_archive) with deterministic-hash embeddings out of the box —
  swappable to ChromaDB / sentence-transformers later.
- **Mock-first.** With zero API keys and no GPU, the entire 11-step Demo
  still runs end-to-end via `mock_provider` + `mock_simulation` +
  `mock_debate`. CI verifies this every PR.
- **Full sedimentation.** Each task writes nine subdirectories under
  `runs/<timestamp>_<task>/` — input / context / per-agent artifacts /
  HITL / events — making every run replayable and auditable.
- **Tools V1 platform.** Agents and Commander share one registry-backed tool
  catalogue with schema validation, config gating, Gate 5 protection,
  audit events, approval records, and rollback snapshots.

## Architecture at a glance

```
┌──────────────────────────────────────────────────────────────────────┐
│ Tier 1  Web Workbench (Next.js 15)                                   │
│   Lab Dashboard · Agent Workbench · Multi-experiment view · HITL     │
├──────────────────────────────────────────────────────────────────────┤
│ Tier 2  API + Bridge (FastAPI)                                       │
│   /api/runs · /api/artifacts · /api/execution · /api/templates …     │
│   bridge/orchestrator drives the RunGraph; agent_registry inverts    │
│   the dependency so bridge never imports concrete agents.            │
├──────────────────────────────────────────────────────────────────────┤
│ Tier 3  Five Agents                                                  │
│   IdeaAgent (debate-on)     →  proposal.v1                           │
│   ExperimentAgent           →  experiment_plan.v1                    │
│   CodingAgent               →  code_spec.v1   (3 LLM backends)       │
│   ExecutionAgent            →  run_log.v1     (≤6 concurrent sims)   │
│   WritingAgent (debate-on)  →  report.v1                             │
├──────────────────────────────────────────────────────────────────────┤
│ Tier 4  Harness (agent-agnostic)                                     │
│   runtime · schema · llm · context · kb · gates · tools · sediment.  │
├──────────────────────────────────────────────────────────────────────┤
│ Tier 5  Storage & Projects                                           │
│   runs/<id>/ (9 subdirs) · knowledge/<zone>/ · workspace/repos/      │
│   projects/<name>/{AGENTS.md, repo_link.yaml, data_gen.py}           │
└──────────────────────────────────────────────────────────────────────┘
```

Strict directional dependency, enforced by **import-linter**:

```
api  →  bridge  →  hitl  →  (agents | execution | workers)  →  storage  →  harness
```

See [`docs/architecture.md`](docs/architecture.md) for diagrams.

## Quickstart (zero-dependency)

No GPU, no LLM keys, no Docker required:

```bash
git clone git@github.com:HarryYangthu/MARS-Multi-Agent-Research-System.git mars
cd mars
cp .env.example .env                 # leaving every key empty is fine — mock fallback
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# === backend ===
PYTHONPATH=backend uvicorn app.main:app --host 127.0.0.1 --port 8000 &

# === frontend ===
cd frontend && npm install --legacy-peer-deps && npm run dev
# open http://localhost:3000
```

Run the canonical 11-step end-to-end demo (mock-mode):

```bash
python scripts/run_demo.py --port 8000 --mock-mode
```

Full acceptance gate (mypy --strict + import-linter + backend/frontend checks +
Tools V1 audit + e2e):

```bash
bash scripts/acceptance.sh
```

## Going real (Hardware E2E lane)

Drop your provider key into `.env` — DeepSeek as an example:

```bash
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
```

Mount your real research code via a symlink (it stays out of the MARS repo,
per CLAUDE.md hard constraint):

```bash
ln -s /path/to/your/code workspace/repos/pimc-current
# update projects/moe-pimc/repo_link.yaml::repo_path
python scripts/ingest_repo.py --project moe-pimc
```

Index reference papers (PDF):

```bash
cp ~/Downloads/*.pdf workspace/uploads/papers/
python scripts/ingest_pdfs.py
```

## Repository layout

```
mars/
├─ README.md / CLAUDE.md / PRODUCT.md / DESIGN.md / ACCEPTANCE.md
├─ pyproject.toml · docker-compose.yml · .env.example
├─ configs/                  # agents/models/tools/gates/knowledge/execution YAML
├─ backend/app/
│  ├─ api/                   # REST + WebSocket
│  ├─ bridge/                # orchestrator · agent_registry · workflow_service
│  ├─ harness/               # runtime · schema · llm · context · kb · gates · tools · sedimentation
│  ├─ agents/                # 5 agents + debate runner
│  ├─ hitl/                  # review_session · approval · audit_log · diff_view
│  ├─ execution/             # mock_simulation · batch_runner · log_streamer · metrics_collector
│  ├─ storage/               # run_store · artifact_store · file_store
│  └─ workers/
├─ frontend/src/
│  ├─ app/                   # Next.js routes — Lab dashboard / Run detail / Multi view / Entries
│  ├─ components/            # TopBar · ProjectsPanel · PipelineOverview · EventLog · KBPanel
│  ├─ lib/                   # api · i18n · socket
│  └─ stores/
├─ projects/moe-pimc/        # AGENTS.md · repo_link.yaml · data_gen.py
├─ workspace/repos/          # real research code (symlinked, gitignored)
├─ workspace/uploads/papers/ # reference PDFs (gitignored)
├─ knowledge/                # 4 KB zones (gitignored after first ingest)
├─ runs/                     # per-task sedimentation (gitignored)
├─ templates/                # artifact templates · code_rules
├─ scripts/                  # dev.sh · run_demo.py · acceptance.sh · ingest_repo.py · ingest_pdfs.py
└─ docs/                     # architecture · agent_io_schema · run_lifecycle · evaluation · phase status
```

## Project status

**V0 acceptance: passing** (the Dev E2E lane). See
[`docs/implementation_report.md`](docs/implementation_report.md) for the full
audit. Highlights:

| | |
|---|---|
| Backend tests | unit / integration / gate passing |
| Frontend checks | typecheck / lint / context smoke passing |
| Tools V1 audit | catalogue / API filters / trace / execution artifacts verified |
| `mypy --strict` | clean |
| `import-linter` contracts | 4 kept, 0 broken |
| Schema compliance | ≥ 95% |
| Baseline matcher recall / precision | 100% / 100% on synthetic set |
| 11-step e2e demo | passes in mock mode without external deps |
| `runs/<id>/` completeness | 9/9 subdirs populated |

## Roadmap (V1 themes)

V0 remains the stable release line (`v0.1.0`). V1 work should be treated as
development until [`ACCEPTANCE_V1.md`](ACCEPTANCE_V1.md) is green.

- **Post-training pipeline** — GRPO trainer, preference-pair construction
  from `runs/<id>/hitl/*`, composite reward (schema validity ×
  baseline preservation × downstream metric).
- **Streaming UX** — token-by-token LLM output for the Coding agent;
  one-click "fill missing schema fields" repair.
- **Real-time training observability** — subprocess stdout → WS, GPU
  utilization curves alongside loss.
- **Multi-project isolation** — per-project `runs/` and `knowledge/`
  segregation, project switcher in the Lab dashboard.
- **Real-vector KB** — drop-in replace the deterministic-hash embedder
  with sentence-transformers / ChromaDB.

## Documentation

- [`PRODUCT.md`](PRODUCT.md) — product definition (5 agents, dual-form, KB zones, decision log)
- [`DESIGN.md`](DESIGN.md) — architecture (tiers, schemas, harness internals, runtime, frontend)
- [`ACCEPTANCE.md`](ACCEPTANCE.md) — V0 acceptance bar (Dev E2E + Hardware E2E)
- [`ACCEPTANCE_V1.md`](ACCEPTANCE_V1.md) — V1 development gate before stable version bump
- [`docs/V1_RELEASE_STATUS.md`](docs/V1_RELEASE_STATUS.md) — latest P3 release-gate result and command
- [`AGENTS.md`](AGENTS.md) / [`CLAUDE.md`](CLAUDE.md) — hard constraints, layout, style rules for coding agents
- [`docs/architecture.md`](docs/architecture.md) — companion diagrams
- [`docs/agent_io_schema.md`](docs/agent_io_schema.md) — artifact/system schemas, fields, examples
- [`docs/run_lifecycle.md`](docs/run_lifecycle.md) — sequence diagram of one task end-to-end
- [`docs/evaluation_system.md`](docs/evaluation_system.md) — systematic evaluation layer design
- [`docs/tools_catalog.md`](docs/tools_catalog.md) — Tools V1 catalogue, APIs, audit records, external smoke
- [`docs/tool_security.md`](docs/tool_security.md) — dispatch order, Gate 5, rollback, redaction, network policy
- [`docs/V1_AGENT_TODO.md`](docs/V1_AGENT_TODO.md) — V1 cleanup and implementation queue
- [`docs/frontend_ux.md`](docs/frontend_ux.md) — P0 UI contract

## License

MIT — see [LICENSE](LICENSE).

## Citation

If you use MARS in academic work, please cite the repo:

```bibtex
@misc{mars2026,
  title  = {MARS: Multi-Agent Research System},
  author = {Yang, Harry},
  year   = {2026},
  url    = {https://github.com/HarryYangthu/MARS-Multi-Agent-Research-System}
}
```
