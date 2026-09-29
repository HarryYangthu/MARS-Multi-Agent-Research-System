# Conversation budget recovery and context packing

## Behavior

Commander conversations charge the configured time allowance against cumulative
model-request durations across turns. Time between requests (including human idle
or review time) is excluded. Existing request timestamps are used on reload;
request, token and cost charges are preserved, including failed/cancelled calls.
Unknown requests still require reconciliation. Remaining active time bounds an
in-flight conversation call. Legacy research runs retain their existing elapsed
window/revision rules; research-contract budgets are unchanged.

The host repository index is navigation material and may be offloaded to a
hash-addressed reference. It is not pinned as the latest source implementation.
Latest source and tool results, task instructions and actual tool failures remain
protected. Observation status is parsed from the host envelope, not quoted output.

Known context/resource failures return a structured HTTP 409 with a user-facing
reason. The failed activity retains that reason; saved user messages are not put
back into the composer for accidental duplicate submission. Unknown exceptions
remain server errors. No automatic task retry or quota reset is introduced.

## Verification (2026-09-29)

- 69 related unit tests: model accounting, runtime context, public errors,
  conversation activities, contract accounting and loop budgets.
- Strict mypy on six changed Python modules; frontend typecheck and production build.
- Repacked a real failed Coding checkpoint with its frozen policy, actual tool
  schema and recorded history: before the fix it reproduces 48,786 > 48,000;
  after the fix it uses 35,509 upper-bound tokens. The soft 31,200 target is not
  reached because protected input is retained. This is packing validation,
  not a restarted Coding execution or a successful simulation.
- A separate diagnostic conversation reused an expired conversation ledger and
  completed a real model call, with no linked research run or tool action.
- Browser verification used an explicit policy revision in that diagnostic ledger
  to exercise the real blocked API path: readable HTTP 409, empty composer,
  no request replay. Restored the diagnostic policy afterward without refunding charges.
