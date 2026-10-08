# Baseline training protocol validation

The reported poor baseline came from a 50-update pipeline acceptance task,
not the reference's 200-full-epoch training protocol. Code inspection confirmed
that the step and epoch entrypoints advance StepLR on different axes. Current
model/data/metric operators match the linked baseline branch.

## Runtime evidence

The original full-training checkpoint was strictly loaded into the current
model and reevaluated on the actual configured capture and historical split.
Its measured RES and APE reproduced the saved historical summary to that
summary's two-decimal precision. This checks compatibility and the current
evaluator, not independent scientific validation. A separate bounded full-epoch
retraining was also launched; its ongoing results are not represented as a
completed run.

The existing actual paper-training receipts now pass read-only result admission.
They omitted the optional duplicated `config.backend` field: their backend is
already bound in the verified host submission digest. Explicit contradictions
remain rejected, and receipt, job, log, summary, curve and hash checks remain in
place. Result limitations explicitly identify sub-epoch runs.

## Checks

- 30 unit/integration checks passed, including read-only admission of the real
  saved two-job task, exact budget/seed checks and Git confirmation invalidation.
- Strict mypy passed for the four changed Python modules.
- Frontend type checking and execution-presentation smoke checks passed.
- Pure configuration regressions cover sibling base defaults, scenario
  precedence, CLI loop overrides, 200-epoch versus 50-update semantics,
  scheduler axes and rejection of insufficient loop capacity.

No provider, model or training-command substitutes were used. Metrics were not
edited, historical run artifacts were not rewritten, and the baseline model was
not modified.
