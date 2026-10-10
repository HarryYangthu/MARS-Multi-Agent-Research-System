# Document format compatibility and failed-draft recovery

Validated on 2026-10-08 for `2026-10-08T0441_static_pimc_verified_baseline_optimization`.

## Behavior

- Document submission/revision arguments coalesce duplicate JSON fields only when their JSON types and values match. Conflicting values remain errors. Ordinary tool calls and review decisions remain strict.
- `replace` is an alias for `set` only at an existing target. A missing `/metadata` prefix is resolved only below an existing metadata field. Missing parents, invalid pointers, stale candidate hashes, and partial operations remain rejected.
- The original model arguments and normalization receipts are retained separately. Compatibility never invents research content or grants acceptance.
- Explicit retry of a settled rejected Idea draft restores the schema/project-bound draft and trace-verified immutable literature observations. Mutable repository reads must be performed again. Unresolved tool operations and unknown model outcomes cannot be silently replayed.
- A seeded draft is revalidated against current requirements before the next model request. Training/held-out separation, evidence, parameter constraints, whole-document validation, and independent review remain required. Acceptance and resource-budget counters are not reset or inherited.
- Recovery messages identify terminal GLM timeouts and document-format exhaustion from actual receipts.

## Verification

260 related tests passed; 10 opt-in tests were skipped because their specific historical archives were not supplied. Tests used pure parser/validator inputs, actual files, and read-only actual traces; no provider, model, or executed-tool substitutes were used.

The current failed task's actual final `replace` arguments parsed successfully with compatibility receipts. The rejected draft and original literature receipts were restored without executing tools, and its training/held-out errors remained blocking. An actual completed Idea archive and an actual quota-failure archive also passed reuse checks.

Strict mypy passed for all eight modified source modules. Whitespace checks passed.

The full repository import-boundary check still reports existing violations in unchanged files:

- `bridge/literature_evidence.py` imports concrete Idea modules.
- `storage/artifact_store.py` and `agents/coding/agent.py` import `execution/handoff_validation.py`.

These pre-existing architecture violations were not changed by this scoped fix; this validation does not claim the entire repository passes CI.

## Runtime handoff

Backend restarted on `127.0.0.1:8010`; frontend remained available on `127.0.0.1:3001`. API and browser both show the actual GLM timeout (two attempts) and the enabled retry action for the existing failed task. Run state, cumulative model-budget ledger, and both original Idea checkpoints remained byte-identical after inspection/restart.

No new research run or model request was started. The user will retry the research stage. This verifies format/recovery behavior, not a completed new research proposal or a successful scientific experiment. External API timeouts and substantive proposal errors can still occur and remain visible.
