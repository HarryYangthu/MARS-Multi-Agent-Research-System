# Completion-driven Coding execution

Validated locally on 2026-09-29.

**2026-10-06 correction:** the aggregate-quota exemption described below was a
defect: Coding could overrun the same ledger enforced by the next Agent. It has
been removed. Completion-driven now controls only local loop counters; all roles
obey run request/time/cost limits and the configured token policy. Historical
receipts remain unchanged. See [research-flow regressions](research-flow-regressions.md).

## Behavior

`coding.loop.completion_driven: true` changes Coding's stopping policy to validated delivery, a real execution blocker, or user cancellation. It removes fixed model-call, tool-dispatch and validation-repair counters. Generic run accounting no longer rejects this scoped execution because of aggregate request, token, cost or elapsed-time ceilings. Counters and actual/conservative usage receipts continue to accumulate; existing charges are never reset.

The setting is scoped to the executing Agent. Other Agents remain bounded. Previously sealed research contracts retain their separate contract admission/accounting limits; this does not rewrite a frozen contract. Per-request network deadlines/retries, malformed-protocol failure handling, unknown-outcome reconciliation, concurrency, context capacity, permissions and baseline protection remain enforced. This setting alone does not repair an oversized protected context or create a candidate workspace.

Coding checks its effective repository before invoking the model. A read-only baseline or missing working directory stops with a concrete error. Recovery uses the same preflight for legacy runs, so exhausted generic quotas do not hide the actual working-directory blocker or invite futile retries. Building context for inspection remains available.

## Checks

- 79 related unit checks passed across completion policy, loop budgets/stopping, recovery, model-attempt accounting, generic pipeline resources and Coding configuration. The new checks use real temporary ledger/configuration files and an actual refused local connection; no successful provider/tool response is fabricated.
- The ledger check records 65 reservations / 195 charged SDK attempts against a configured one-request ceiling, with expired time and token quotas. Completion mode permits these reservations while preserving all charges and rejecting concurrent reservations; bounded mode still refuses them.
- A real temporary read-only repository is rejected before any model ledger is created, with its code untouched.
- Full strict mypy passed across 666 files; the final modified test and Coding module were checked again. All four import contracts passed.
- Local runtime configuration enabled only `coding.loop.completion_driven`. Backend reload occurred after checking 15 runs and four conversations had no active owner or message processing.
- The existing failed PIMC run's recovery endpoint now reports that its writable experimental copy is missing. Its 58 model rows / 58 charged attempts / 519,685 charged tokens remained byte-for-byte unchanged. No model retry, baseline modification or simulation was started.

These checks establish policy/accounting and blocker behavior, not successful PIMC code generation or experiment performance. The existing task still needs a writable experimental copy before Coding can proceed.

Follow-up deployment policy: cumulative token ceilings are now statistics-only for all roles, including contract-bound runs, without rewriting frozen contracts. See [token statistics and context rules](token-statistics-and-context.md). Other contract limits remain independent.
