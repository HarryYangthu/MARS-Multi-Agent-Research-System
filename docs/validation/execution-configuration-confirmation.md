# Execution configuration confirmation

The execution planner prepares a proposal; it does not launch training. After its plan is approved, manual research waits for a separate configuration confirmation in the main conversation. The dialog inherits current settings and shows the actual backend, repository and Git branch, data, interpreter, limits, experiment seeds and the configuration files delivered by coding.

The preview and batch use the same preparation function. The confirmation receipt binds the run, project, execution attempt, approved documents, changed code, data file identity, execution configuration and resource policy. A changed input invalidates the receipt. The batch checks this admission before opening TensorBoard or launching commands. Repeated confirmation reuses its receipt and the single owned driver. Approval HTTP responses acknowledge scheduling without waiting for configuration input or training output. An interrupted response can be reconciled or explicitly retried with the same confirmation identity.

Explicit automatic mode records `auto_approve` as its confirmation source only after input checks pass. Manual mode requires user acknowledgement. Missing plans, ambiguous seeds, backend conflicts, inaccessible files, omitted experiments, mismatched training configurations and seed/step mismatches fail closed. SSH credentials are not returned to the browser. Waiting remains cancellable; restarting does not silently launch simulations.

## Current task diagnosis

Task `2026-10-06T0240_static_pimc_train_side_improvement` failed while preparing its execution plan. The budget ledger charges 428 model attempts against a limit of 100; reserving the next request requires three attempts. The rejected execution invocation contains no SDK attempt and reports `request_sent: false`. No approved execution plan or batch summary was produced. This is a planning admission failure, not a failed training process.

The live configuration preview separately identified these future launch blockers:

- Runtime backend `paper_static` differs from configured backend `local_command`.
- The upstream experiment plan uses the text “同基线种子” instead of a concrete seed.
- The adapter reads a generic `configs/static.yaml` with 10 training epochs and no explicit seed. Coding delivered five experiment YAML files with seed 2026 and 50 epochs.

These configuration issues did not cause the already rejected model request. They must be resolved before interpreting any subsequent simulation as the approved experiment. This change does not raise/reset budgets, retry the research invocation, change the external research code or claim any scientific improvement.

## Verification

Targeted regression suite: 79 passed, 1 skipped. The skip requires an existing real research checkpoint; no execution substitute was introduced. Coverage includes immutable/idempotent confirmations, changes to data/configuration/approved plans/code, real Git branch binding and checkpoint commits, missing approval, wrong projects, cancellation/restart, explicit automatic mode, duplicate requests, immediate HTTP acknowledgement and actual local numerical experiments with measured receipts.

Strict Python typing passed for five changed backend modules and the new test module. Frontend TypeScript checking passed. Live browser verification showed the main-chat dialog, actual settings and disabled launch controls for the current blocked task. Original budget and approved-code hashes remained unchanged:

- Budget ledger: `e4e3a03ec41752349ab0134d8063eabccaa1b2bc530a2d7c4a9c528d3df762ae`.
- Approved code document: `bddacfc4f1acbd05466946027006d165d0c7f17f83cb8042a768d47e9a270d2f`.

These are targeted checks, not a claim that every repository check or the actual PIMC research experiment completed.
