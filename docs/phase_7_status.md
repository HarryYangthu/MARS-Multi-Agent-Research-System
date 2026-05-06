# Phase 7 Status — End-to-End Demo + Acceptance + Implementation Report

## Sub-acceptance (ACCEPTANCE §13)

| Item | Status | Evidence |
|---|---|---|
| `scripts/run_demo.py` (11 steps) | ✓ | one-shot CLI demo against running backend; waits for done states, validates 9 subdirs |
| `scripts/acceptance.sh` (8 steps) | ✓ | exits 0 in `/tmp/mars-acceptance-final.log` |
| `mypy --strict backend/` | ✓ | "Success: no issues found in 132 source files" (incl. tests) |
| `lint-imports` 4 contracts | ✓ | "Contracts: 4 kept, 0 broken" |
| Unit + integration tests | ✓ | **209 passed in 4.5 s** |
| Schema compliance ≥95% | ✓ | aggregate test asserts ≥95% on the in-suite valid samples |
| Gate tests (5 gates) | ✓ | `tests/gate/` — 12 cases incl. Gate 5 dispatch short-circuit |
| Baseline matcher recall/precision | ✓ | `tests/baseline/` — 100% recall, 100% precision on synthetic 10+5 set |
| 11-step e2e demo (zero deps) | ✓ | every state ends `done`, all 9 subdirs populated |
| `runs/` completeness | ✓ | 9/9 subdirs have content |
| `docs/architecture.md` | ✓ | written |
| `docs/agent_io_schema.md` | ✓ | written |
| `docs/run_lifecycle.md` | ✓ | written |
| `docs/frontend_ux.md` | ✓ | written |
| `posttrain/README.md` | ✓ | V0 boundary + V1 hooks documented |
| `templates/code_rules/pimc_python.md` | ✓ | already in place since Phase 5 |
| `docs/implementation_report.md` | ✓ | filled per ACCEPTANCE §12 — 9 sections incl. risk callouts on spec inconsistencies |

## End-to-end checkpoint (Phase 7)

This **is** the final e2e — the entire ACCEPTANCE.md §2 11-step main script
runs without human babysitting under `--mock-mode`. `scripts/acceptance.sh`
plays the full chain: static analysis → tests → server boot → demo →
runs/ completeness, all green.

## Acceptance log (head)

```
===== 1. mypy --strict =====
Success: no issues found in 132 source files

===== 2. import-linter =====
Contracts: 4 kept, 0 broken.

===== 3. unit + integration tests =====
.. (66 unit + 16 integration) all pass

===== 4. schema compliance ≥95% =====
.. (100+ parametrized) all pass

===== 5. gate tests =====
............                                                             [100%]

===== 6. baseline matcher recall/precision =====
...                                                                      [100%]

===== 7. e2e demo (zero external deps) =====
[Step 1]–[Step 11] — all states `done`, 9/9 subdirs populated

===== 8. runs/ completeness =====
9/9 ✓

===== ✅ V0 acceptance passed =====
```

## Artifacts produced by the demo

```
runs/<ts>_acceptance_demo/
├─ run_meta.json
├─ input/         user_request.md
├─ context/       <agent>_context_pack.v1.json + <agent>_context_snapshot.v1.md ×5
├─ idea/          idea_proposal.{v1,approved}.md
├─ experiment/    experiment_plan.{v1,approved}.md
├─ coding/        code_spec.{v1,approved}.md
├─ execution/     run_log.{v1,approved}.md + per-ablation run_log_<exp>.v1.md + curves/<exp>_loss.json + metrics.json + batch_summary.json
├─ writing/       research_report.{v1,approved}.md
├─ hitl/          review_log.jsonl
└─ events/        agent_events.jsonl + websocket_events.jsonl
```

Plus `knowledge/{literature,methodology,code_assets,run_archive}/_index.json` populated by sedimentation hooks.
