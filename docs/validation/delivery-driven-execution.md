# Coding delivery to execution intake

Validated on 2026-10-10.

## Problem and resulting behavior

A concrete coding-and-simulation task can intentionally skip Experiment. Coding
previously accepted its runtime bindings without a design document, while
Execution always required an approved experiment plan. The task consequently
failed before creating any jobs, even though the approved code specification
already contained the runnable baseline and candidate configurations.

Execution now accepts complete, approved Coding delivery directly when Experiment
was skipped. It creates the execution manifest deterministically, without a model
request or fabricated experiment document. When the authoritative dependency graph
requires Experiment, the approved plan remains mandatory and constrains the job
names, seeds, budgets, configuration bindings, and provenance.

Coding generation, human approval, execution preview, and actual launch share the
same handoff checks. Missing runtime jobs, unknown host commands, incompatible
entrypoints, or invalid configuration bindings are rejected before approval.
Standalone coding tasks do not need a simulation manifest. A direct execution
manifest records the SHA-256 of the complete approved code specification, so a
later edit requires fresh intake and configuration confirmation.

The pre-simulation configuration confirmation gate remains in place. Recovery now
also requests an immediate refresh of the main conversation's run state, whose
polling otherwise stops after a terminal failure.

## Verification

- 64 related unit tests passed across coding execution intake, execution plan
  contracts, experiment handoff consistency, and execution confirmation.
- Strict Python type checks passed for all 13 changed Python files.
- Frontend TypeScript checking passed after adding the recovery refresh callback.
- The new intake tests use real temporary artifacts, durable run graphs, folder
  projects, and registered host command configuration. They verify incomplete
  delivery rejection, required-plan protection, direct preview, approval,
  confirmation idempotency, and invalidation after an approved code change.
- The existing failed layer-2 kernel 17-to-22 task was retried through the real
  browser after restarting the backend with this change. It reached
  `waiting_execution_confirmation`, with Execution approved, two validated
  experiments, `can_confirm=true`, and an empty blocker list.
- The real configuration preview showed the actual branch, existing config files,
  data path, seed 2026, and two approved epochs per experiment. The browser showed
  the configuration confirmation controls and the current recovered state.

## Acceptance boundary

The current task has not launched its simulation jobs. Its job list is empty and
configuration confirmation is still pending. These checks verify intake and
recovery; they do not claim simulation performance, research success, or report
quality. Runtime artifacts and screenshots stay local and are not committed.
