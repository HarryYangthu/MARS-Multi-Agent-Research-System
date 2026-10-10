# Execution batch deadline and interrupted-job recovery

## Reproduced boundary

The real Test6 run `2026-10-09T1706_test6_lr_research` approved six sequential
20-epoch paper-static jobs, each with a 900-second adapter limit. The first two
completed with actual summaries/receipts. At exactly 600 seconds, the generic
`execution.batch_runner` dispatch deadline cancelled the third job. The original
service traceback points to `harness/tools/registry.py`'s `asyncio.wait_for`;
the experiment itself had not reached its individual limit.

## Policy

The bridge revalidates the confirmed execution preview and selects a private
registry fork. Its finite batch deadline is the sum of the actual per-job
limits plus the configured batch dispatch allowance for setup, evidence and
cleanup. The sequential upper bound also safely accommodates approved parallel
jobs. For this matrix the outer limit is 6000 seconds; each adapter retains
its 900-second limit. The global registry, gates, argument/output schemas and
permissions remain unchanged. Models cannot supply a timeout override through
arguments or `ToolContext.extra`.

A dispatch timeout has a nonempty cause, `timeout` status, elapsed limit and
`tool_dispatch` scope. Cancellation is acknowledged only after child-process
cleanup. An explicitly retried interrupted execution keeps its previously
approved deterministic intake and confirmation identity. Completed jobs still
verify actual receipts and evidence hashes before reuse; only input-identical
interrupted jobs are rearmed, with the old journal and claim retained under
`execution/jobs/retry_history`. Running, failed, changed-input and explicitly
unclean records are refused. Ordinary artifact revision still regenerates an
intake when it is not a failed interrupted batch.

## Checks

`test_execution_batch_deadline.py` covers a six-job budget, heterogeneous limits,
invalid/nonfinite limits, unconfirmed inputs, registry isolation, a real Python
process timeout with verified termination, and durable retry/archive guards.
No provider, service or execution-success substitute is used. Confirmation and
run-recovery regressions are also run. The desktop Test6 acceptance log records
live recovery/results separately; unit checks alone are not a research result.

Execution runtime/preflight failures are also saved as identity-checked
`task.failure.v1` records under `input/node_failures`. Recovery reads this durable
cause, including the dispatch scope, instead of depending on an open WebSocket.
A different-run or corrupt failure record blocks recovery; its contents affect
the recovery token. Tests cover an actual missing-repository preflight refusal
through the execution boundary and rereading its persisted failure, alongside
pure failure-envelope identity checks.
