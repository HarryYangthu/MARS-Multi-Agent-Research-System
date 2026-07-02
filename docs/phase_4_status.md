# Phase 4 Status — HITL Backend + Frontend Workbench

## Sub-acceptance

| Item | Status | Evidence |
|---|---|---|
| `hitl/review_session.py` | ✓ | Per-(run,agent) sessions w/ approval/rejection/regenerate `asyncio.Event`s + audit-trail writes |
| `hitl/audit_log.py` | ✓ | `runs/<id>/hitl/review_log.jsonl`; structured `AuditEntry(action, actor, detail)` |
| `hitl/diff_view.py` | ✓ | `unified()` for monaco diff display |
| `hitl/approval.py` | ✓ | Approve / reject helpers that publish WS events |
| `hitl/revision_loop.py` | ✓ | `apply_human_edit()` produces a new `vN` even when invalid (so UI can show errors) |
| Orchestrator parks at `WAITING_REVIEW` | ✓ | `_await_hitl_or_auto()`; auto-approve flag preserved for tests/CI |
| Reject → node `FAILED` | ✓ | `test_reject_marks_run_failed` covers it |
| `api/artifacts.py` REST | ✓ | versions / get / diff / edit / approve / reject / comment / audit / pending |
| Frontend Dashboard (6 cards) | ✓ | `frontend/src/app/page.tsx`: Pipeline + 5 Agent cards |
| `runs/new` form | ✓ | Suspense-wrapped useSearchParams; creates + starts run |
| `runs/[id]` Workbench | ✓ | Sidebar timeline + state badges + WS subscribe + textarea editor + approve/reject/save |
| `runs/` list page | ✓ | `runs/page.tsx` |
| Validation badge | ✓ | Inline schema validation status + per-error list |
| Layered architecture: bridge above hitl above agents | ✓ | `.importlinter` updated; `bridge/` may use `hitl/`, `hitl/` is below |
| `mypy --strict` clean | ✓ | "Success: no issues found in 70 source files" |
| `lint-imports` 4/4 KEPT | ✓ | Same |
| `next build` clean | ✓ | 5 routes generated, no errors |
| Pytest all green | ✓ | 185 passed (incl. 2 HITL e2e tests) |
| HTTP HITL e2e | ✓ | `POST /api/runs` (auto_approve=false) → orchestrator parks at waiting_review → `POST /api/artifacts/.../approve` advances → 5 stages chain through to `done`; audit log records 5 approves |

## Test counts (cumulative)

```
backend/tests/                                              185 passed
   ↳ schema/                                              ≈ 100
   ↳ unit/                                                   46
   ↳ integration/                                            10  (incl. test_hitl_flow.py 2)
```

## How to verify

```
source .venv/bin/activate
mypy --strict backend/app/                # → clean
PYTHONPATH=backend lint-imports           # → 4 kept, 0 broken
PYTHONPATH=backend pytest backend/tests/  # → 185 passed
PYTHONPATH=backend uvicorn app.main:app --host 127.0.0.1 --port 8765 &

RID=$(curl -sX POST http://127.0.0.1:8765/api/runs \
   -H 'Content-Type: application/json' \
   -d '{"task":"phase4","project":"pimc","user_request":"test"}' \
   | python -c 'import json,sys;print(json.load(sys.stdin)["run_id"])')
curl -sX POST http://127.0.0.1:8765/api/runs/$RID/start
# → state shows idea=waiting_review
curl -sX POST http://127.0.0.1:8765/api/artifacts/$RID/idea/idea_proposal/v1/approve
# → idea=done, experiment=waiting_review (chain)

# Frontend:
cd frontend && npm install --legacy-peer-deps && npm run dev
# http://localhost:3000  →  click any card → start run → review/approve in UI
```

## End-to-end checkpoint (Phase 4)

E2E at this Phase = "Frontend Dashboard → start Pipeline run → orchestrator parks at
each stage → user clicks Approve in UI → next stage runs → 5 stages chain to done →
audit log + approved artifacts visible".

The **HTTP-driven** version of the chain (above) demonstrates the wiring without
needing a browser. The frontend has been compiled (Next.js 15 production build
succeeds; 5 routes). To exercise the visual UI, start backend + `npm run dev`
and click through the cards.

## Notes / decisions

- **Layered fix**: bridge can now import hitl (so orchestrator drives review sessions). hitl no longer imports bridge — the `RunnableAgent` re-export was dead and removed.
- **Auto-approve flag retained**: smoke tests and the Phase 7 acceptance demo still set `auto_approve=True`. The default for production runs is `auto_approve=False` so HITL is in the chain.
- **Editor MVP**: in-page `textarea` not the @uiw monaco editor — the textarea path is server-renderable and avoids large editor bundle for the first cut. Phase 6/7 may upgrade once we have stable artifact stems.
- **Polling fallback**: WS state events drive updates; a 2.5s `getRun()` poll backs them up so a missed event still recovers the UI.
- **Suspense wrapper**: `useSearchParams` requires a `<Suspense>` boundary in Next 15 production builds; `runs/new` is wrapped accordingly.
- **Frontend build is offline-safe**: no images / external fonts / tracking scripts — Dev E2E still zero-dependency.
