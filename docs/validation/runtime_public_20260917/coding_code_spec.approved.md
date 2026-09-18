---
schema: code_spec.v1
project: folder_3d60b6ac4cc842a498b7e65534660f3d
agent: coding
target_lang: python
upstream_artifact: experiment/experiment_plan.approved.md
baseline_compat:
  preserved: true
  rationale: 'Only candidate.py was modified: DEGREE changed from 1 to 2 and REGULARIZATION
    changed from 0.01 to 0.0. Protected paths baseline/, data/, .mars/, diagnostics.yaml,
    the evaluator, and the train/held-out split remain untouched.'
files_changed:
- path: candidate.py
  type: modified
  risk: low
new_dependencies: []
test_coverage:
  unit_tests_added: 0
  baseline_smoke_test: pass
---

# Code Spec: CPU polynomial ridge candidate configuration

## 1. Summary
Implemented the approved experiment plan by modifying only `candidate.py`:

- `DEGREE` changed from `1` to `2`
- `REGULARIZATION` changed from `0.01` to `0.0`

Protected paths were not modified. No new dependencies were added.

## 2. Patch
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

## 3. Execution evidence
- The default configured test command `candidate_contract` passed:
  `Syntax, candidate parameter constraints, and protected input checks passed; no experiment executed.`
- The attempted experiment command `code.test_runner(command_id="experiment")` failed:
  `no test commands configured; not executed`

Therefore the real seed=0 experiment was not executed in this environment. The four requested dependent metrics are unmeasured:

| Metric | Status |
|---|---|
| `baseline_mse` | skipped/unavailable |
| `candidate_mse` | skipped/unavailable |
| `improvement` | skipped/unavailable |
| `candidate_terms` | skipped/unavailable |

## 4. Data observation
`data/dataset.json` was read. All 11 points satisfy `y = 1.5*x^2 - 0.75*x + 0.5`; this supports the proposal hypothesis analytically, but it is not a substitute for the evaluator-run seed=0 fit with training-only normalization.

## 5. Conclusion
The candidate is configured as planned and the candidate contract smoke test passes. No measured held-out MSE improvement can be claimed because the configured real experiment command is unavailable in this environment.