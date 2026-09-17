---
schema: code_spec.v1
project: folder_6dffbd8f7afc43dc99bc9d814673e3c9
agent: coding
upstream_artifact: experiment/experiment_plan.approved.md
target_lang: python
baseline_compat:
  preserved: true
  rationale: 'Only candidate.py DEGREE and REGULARIZATION were modified. baseline/baseline.py,
    data/dataset.json, .mars/, diagnostics.yaml, the evaluator, and the train/held-out
    split remain untouched. candidate.py values remain within allowed constraints:
    DEGREE=2 (integer 1..5) and REGULARIZATION=0.0 (finite >=0).'
files_changed:
- path: candidate.py
  type: modified
  risk: low
new_dependencies: []
test_coverage:
  unit_tests_added: 0
  baseline_smoke_test: skipped
---

# Code Spec: candidate.py polynomial ridge degree and regularization

## Actual diff

```diff
--- a/candidate.py
+++ b/candidate.py
@@ -1,3 +1,3 @@
 # Actual initial polynomial ridge model; the baseline remains immutable.
-DEGREE = 1
-REGULARIZATION = 0.01
+DEGREE = 2
+REGULARIZATION = 0.0
```

## Files changed

| Path | Type | Risk | Reason |
| --- | --- | --- | --- |
| `candidate.py` | modified | low | Set candidate polynomial degree to 2 and ridge regularization to 0.0, per the approved experiment plan. |

No protected files were changed. `baseline/baseline.py` was read and remains `DEGREE = 1`, `REGULARIZATION = 0.01`. `data/dataset.json` was read and not modified.

## Evidence receipts

- `code.repo_reader candidate.py`: observed initial `DEGREE = 1`, `REGULARIZATION = 0.01`.
- `code.repo_reader baseline/baseline.py`: observed immutable baseline `DEGREE = 1`, `REGULARIZATION = 0.01`.
- `code.repo_reader data/dataset.json`: observed exact x/y samples; the points are consistent with `y = 1.5*x^2 - 0.75*x + 0.5` for the listed 11 samples. This is data inspection only, not experiment output.
- `code.write_file candidate.py`: wrote `DEGREE = 2`, `REGULARIZATION = 0.0`.
- `code.test_runner` with no command id: executed `check_candidate.py`, return code 0. Output: `Syntax, candidate parameter constraints, and protected input checks passed; no experiment executed.`
- `code.test_runner` with `command_id: "candidate_evaluation"`: failed with `no test commands configured; not executed` and `commands: []`.

## Experiment comparison

No real MSE evaluation was executed. The configured real local evaluation command was not available:

- `baseline_mse`: not measured
- `candidate_mse`: not measured
- `improvement`: not measured
- `candidate_terms`: not measured

Therefore there is no measured evidence of held-out MSE improvement. The candidate code change is syntactically valid and passes contract/protected-input checks, but the actual request-bound metric experiment did not run. No experimental success or PIMC/scientific novelty is claimed.

## Constraints

- `DEGREE = 2`: valid integer in 1..5.
- `REGULARIZATION = 0.0`: finite and >= 0.
- Baseline, data, `.mars/`, `diagnostics.yaml`, evaluator, and train/held-out split were preserved.
- No new dependencies were added.
