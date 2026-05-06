# Phase 5 Status — Context + KB + Sedimentation + 5 Gates

## Sub-acceptance

| Item | Status | Evidence |
|---|---|---|
| `harness/context/system_layer.py` | ✓ | Static role + hard constraints + output schema render |
| `harness/context/project_layer.py` | ✓ | Reads `projects/<name>/{AGENTS.md, project.yaml, repo_link.yaml}` |
| `harness/context/task_layer.py` | ✓ | user_request + KB excerpts + upstream handoff |
| `harness/context/loader.py` | ✓ | `build_context()` returns 3-layer `ContextPack` with `render()` and `to_manifest_dict()` |
| `harness/context/manifest.py` | ✓ | Writes `<agent>_context_pack.vN.json` + `<agent>_context_snapshot.vN.md` |
| `harness/context/compressor.py` | ✓ | hier_summary / reference / relevance_prune (V0 stand-ins) |
| `harness/kb/embedder.py` | ✓ | Deterministic 256-d hash embedder (zero network/model deps) |
| `harness/kb/stores.py` | ✓ | 4 zones (literature / methodology / code_assets / run_archive); JSON-persisted; ChromaDB-equivalent surface |
| `harness/kb/ingester.py` | ✓ | chunk + embed + write |
| `harness/kb/retriever.py` | ✓ | cross-zone top-k |
| `harness/kb/baseline_matcher.py` | ✓ | cosine vs run_archive; threshold via configs/knowledge.yaml |
| `harness/kb/fingerprint.py` | ✓ | SHA256 over canonicalized (plan, code_spec, metric keys) |
| `harness/kb/memory_writer.py` | ✓ | `write_to_zone()` |
| `harness/sedimentation/extractors/` | ✓ | 5 extractors: idea→literature+methodology / experiment→methodology / coding→code_assets / execution→run_archive (with fingerprint) / writing→methodology |
| `harness/sedimentation/hooks.py` | ✓ | `on_agent_completed()` parses frontmatter, dispatches via REGISTRY |
| ★ `harness/tools/registry.py` (Gate 5 hook) | ✓ | `dispatch()` runs registered gates **before** the tool fn; `Gate 5 → block` short-circuits |
| `harness/gates/{plan_finalized,large_refactor,experiment_launch,conclusion_output}.py` | ✓ | All four flow gates implemented |
| ★ `harness/gates/baseline_compatibility.py` (Gate 5) | ✓ | Reads `projects/<name>/repo_link.yaml::protected_paths` + `forward(x, stream_label)` regex; supports class-level `path:Class` patterns |
| `projects/moe-pimc/{project.yaml, repo_link.yaml, AGENTS.md, data_gen.py}` | ✓ | All present |
| `templates/code_rules/pimc_python.md` | ✓ | Tensor shape comments / mypy strict / loguru / no hardcoded magic numbers |
| `workspace/repos/pimc-stub/` | ✓ | `libs/Model.py` (Paper_Total_0327 + Paper_Router_v2) + main.py + baseline/ + production_interface/ |
| `configs/{knowledge,gates}.yaml` | ✓ | thresholds + zone chunk config + monitored_tools |
| Gate 5 unit tests | ✓ | `tests/gate/test_gate_5_baseline_compatibility.py`: 8 cases incl. ★ dispatch short-circuit |
| Gates 1-4 unit tests | ✓ | `tests/gate/test_gates_1_to_4.py` |
| Baseline matcher recall ≥80% / precision ≥90% | ✓ | `tests/baseline/test_baseline_matcher.py::test_recall_and_precision_targets` |
| Sedimentation tests | ✓ | `tests/unit/test_sedimentation.py` |
| Context loader tests | ✓ | `tests/unit/test_context_loader.py` |
| `mypy --strict` clean | ✓ | "Success: no issues found in 92 source files" |
| `lint-imports` 4/4 KEPT | ✓ | All four contracts; harness no longer imports storage |
| Pytest all green | ✓ | 204 passed |
| HTTP e2e | ✓ | Run produces 5 context manifests + populates all 4 KB zones (lit:1 / method:3 / code:1 / archive:1 with fingerprint) |

## Test counts (cumulative)

```
backend/tests/                                              204 passed
   ↳ schema/                                              ≈ 100
   ↳ unit/                                                   55  (+9 Phase 5: context + sedimentation + gates dispatch)
   ↳ integration/                                            10
   ↳ gate/                                                   12  (NEW: 4 + 8)
   ↳ baseline/                                                3  (NEW)
```

## How to verify

```
source .venv/bin/activate
mypy --strict backend/app/                # → clean
PYTHONPATH=backend lint-imports           # → 4 kept, 0 broken
PYTHONPATH=backend pytest backend/tests/ -q
PYTHONPATH=backend uvicorn app.main:app --host 127.0.0.1 --port 8765 &
# trigger a HITL run, approve through, then:
ls runs/<RID>/context/                    # → idea/exp/coding/exec/writing context_pack.v1
cat knowledge/methodology/_index.json     # → ≥3 sedimented chunks
cat knowledge/run_archive/_index.json     # → 1 chunk with fingerprint_hash
```

## End-to-end checkpoint (Phase 5)

E2E at this Phase = "5-Agent Pipeline + HITL + Context Manifest + 4-zone KB
sedimentation + 5 Gates (4 flow gates + 1 dispatch-path gate) + Baseline
fingerprint write".

The HTTP run above demonstrates all of these. Gate 5 specifically is exercised
in the dispatch unit test (`test_gate_runs_inside_dispatch_and_blocks`) — it
returns `blocked_by_gate=GATE_ID` before the tool fn runs.

## Notes / decisions

- **Gate 5 hook position**: `harness/tools/registry.py::ToolRegistry.dispatch()` calls registered gate checks BEFORE the tool fn — exactly as DESIGN §6 / §3 specifies. Gate is wired automatically when `get_registry()` is first called.
- **Class-level protected paths**: patterns like `libs/Model.py:Paper_Total_0327` only fire when the diff *also references the class name*. Plain path patterns (`baseline/`, `production_interface/`) fire on file-touch alone.
- **forward(x, stream_label) check**: regex pair — fires on `def forward(self, x, X, ...)` *unless* X is literally `stream_label`. False-positive risk minimal because the project uses the literal name everywhere.
- **KB store provider**: V0 ships a deterministic-hash embedder + JSON-persisted store. Hardware E2E can swap to ChromaDB by extending `stores.py` (V1).
- **Layered fix**: gates / context were initially using `RunHandle` from storage, breaking the layered contract (harness must not depend on storage). Switched to plain `Path` arguments; the orchestrator does the `RunHandle.root → Path` adapt.
- **Sedimentation runs after each agent**: the `agent_runner.py` post-write hook calls `on_agent_completed()` which dispatches via the per-agent extractor. KB writes are best-effort (failures are logged, not fatal).
