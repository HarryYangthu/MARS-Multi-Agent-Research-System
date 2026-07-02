# Frontend P0/P1 — UX reference

> Maps PRODUCT.md §10 P0 list to the actual Next.js routes shipped in V0.
> P1 adds operator visibility without making GPU, LangSmith, or external
> services required for the mock-first demo.

## Routes

| Route | Component | Purpose |
|---|---|---|
| `/` | `app/page.tsx` (Dashboard) | 6 cards — 1 Pipeline + 5 Standalone Agent entries |
| `/runs` | `app/runs/page.tsx` | Flat list of all runs sorted newest-first |
| `/runs/new?entrypoint=...` | `app/runs/new/page.tsx` | Form to create + start a run, pre-filled with the chosen entrypoint |
| `/runs/[id]` | `app/runs/[id]/page.tsx` (Workbench) | Per-stage timeline + Markdown editor + HITL Approve/Reject/Save |
| `/runs/[id]/multi` | `app/runs/[id]/multi/page.tsx` | MultiExperimentView — 1-6 mini SVG charts + summary + failures |

## Component map (Workbench)

```
┌─────────────────────────────────────────────────────────────────┐
│  Sidebar (260px)               │  Main panel                    │
│                                │                                │
│  ← Dashboard   Multi view →    │  [agent name] Workbench        │
│  Run task name                 │  artifact path / status        │
│  run_id                        │  [Save edit] [Reject] [Approve]│
│                                │                                │
│  ▢ idea          [WAITING ✦]   │  Schema validation badge       │
│  ▢ experiment    [PENDING]     │  ┌──────────────────────────┐  │
│  ▢ coding        [PENDING]     │  │                          │  │
│  ▢ execution     [PENDING]     │  │   <textarea> with the    │  │
│  ▢ writing       [PENDING]     │  │   raw artifact text      │  │
│                                │  │   (frontmatter + body)   │  │
│  EVENTS                        │  │                          │  │
│  channel: payload (truncated)  │  └──────────────────────────┘  │
│  channel: payload (truncated)  │                                │
│  …                             │                                │
└─────────────────────────────────────────────────────────────────┘
```

## State badges

```
PENDING      slate-700
RUNNING      amber-500/30
WAITING…     fuchsia-500/30
APPROVED     emerald-500/30
DONE         emerald-500/40
FAILED       red-500/40
SKIPPED      slate-800
```

## HITL actions

- **Save edit (new version)** — sends a POST `/api/artifacts/.../edit` with the textarea content; backend writes a new vN, returns the latest view, validation badge re-renders.
- **Approve** — sends POST `.../approve`. Backend promotes vN→approved.md, sets the review session's approval_event, orchestrator transitions agent → APPROVED → DONE → next agent runs.
- **Reject** — sends POST `.../reject` with a reason; orchestrator transitions agent → FAILED.

## WebSocket subscriptions

`runs/[id]/page.tsx` opens one socket: `/ws/runs/<run_id>`. Events the page reacts to:

| Channel | Effect |
|---|---|
| `run.<id>.agent_state` | Update the sidebar state badge live |
| `run.<id>.hitl` | Highlight the relevant agent (review_required) |
| `run.lifecycle` | Show "completed" toast (planned — V0 just appends to events list) |

`runs/[id]/multi/page.tsx` polls `/api/execution/<id>/curves` every 2 s. WS streaming hooks for per-experiment ticks are wired but the panel itself is poll-based for simplicity in V0; switching to WS streaming is straight-line work in V2.

## Validation badge contract

```
✓ Schema proposal.v1 valid · version v2     (emerald)
✗ Schema proposal.v1 invalid:                (red)
   - /research_question: shorter than minLength
   - /metrics/primary: required
```

The error list is exactly the structured `ValidationError(path, message)` from the backend's `validator.py`. Click a path → editor scrolls to the offending key (planned V2).

## Frontend not in V0 (P1 items)

- GPU resource panel — shipped as TopBar `Ops` drawer backed by
  `GET /api/runtime/status`; no GPU returns a structured CPU/mock fallback.
- LangSmith trace embedding — shipped as an optional `Ops` drawer embed/link
  when LangSmith is enabled and configured; file-backed traces remain primary.
- Server config advanced drawer — shipped as a sanitized read-only `Ops`
  drawer with runtime, tools, context, provider-secret booleans, and MCP status.
- Multi-project switcher — shipped in P2 as a TopBar selector backed by
  `GET /api/projects`; selected project is stored locally and scopes run list,
  Commander conversation creation, Context Workbench preview, readiness, and
  runtime status.
- i18n — base zh/en toggle exists; full copy coverage remains incremental.

These are explicitly out per ACCEPTANCE.md §1 V0-不做 list.
