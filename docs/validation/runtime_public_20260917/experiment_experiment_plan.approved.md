---
schema: experiment_plan.v1
project: folder_3d60b6ac4cc842a498b7e65534660f3d
agent: experiment
upstream_artifact: idea/idea_proposal.approved.md
variables:
  independent:
  - DEGREE
  - REGULARIZATION
  controlled:
  - seed=0
  - train_samples=8
  - heldout_samples=3
  - training_only_normalization
  - dataset=20260917T193218_folder_3d60b6ac4cc842a498b7e65534660f3d_dataset_6a49e7bd
  - solver=augmented_least_squares_reorthogonalized_QR
  - baseline=baseline/baseline.py DEGREE=1 REGULARIZATION=0.01
  - target_file=candidate.py
  dependent:
  - baseline_mse
  - candidate_mse
  - improvement
  - candidate_terms
metrics:
  primary: candidate_mse
  secondary:
  - baseline_mse
  - improvement
  - candidate_terms
baseline_ref:
  matched_run_id: null
  match_score: 0.0
  reuse_decision: rerun
ablations:
- name: quadratic_ols_candidate_vs_linear_ridge_baseline
  config:
    DEGREE: 2
    REGULARIZATION: 0.0
    fallback_regularization: 1.0e-09
    seed: 0
    target_file: candidate.py
estimated_runs: 1
estimated_gpu_hours: 0.0
---

# CPU Regression Engineering Fixture — Experiment Plan

## 1. Purpose and hypothesis

This plan operationalizes the upstream idea proposal for the public CPU polynomial ridge regression fixture. It does not claim PIMC physics or research novelty.

- **Hypothesis under test**: on the fixed synthetic numerical dataset, changing `candidate.py` from the immutable baseline configuration (`DEGREE=1`, `REGULARIZATION=0.01`) to `DEGREE=2`, `REGULARIZATION=0.0` will fit the apparent noiseless quadratic function `y = 1.5*x^2 - 0.75*x + 0.5`, yielding lower held-out MSE than the fixed linear ridge baseline.
- **Expected mechanism**: an unregularized quadratic ordinary least squares fit can exactly recover a noiseless quadratic function when the training design matrix is well conditioned. Upstream idea analysis reports all 11 dataset points satisfy that quadratic function; that analytic claim is accepted as the proposal hypothesis, but the final outcome is pending actual local execution.

## 2. Baseline matching and prior memory

- `knowledge.baseline_match` returned no matched verified runs: `matched_run_id=null`, `match_score=0.0`, `recommended_action="rerun"`, `reuse_recommended=false`.
- `knowledge.experiment_memory` returned no prior project experiment designs or measured results matching this CPU regression fixture.
- Therefore this is a fresh **rerun**, not a reuse or modification of a measured baseline artifact.

## 3. Variables

### Independent variables
- `DEGREE`: integer `1..5`. Candidate is requested to use `DEGREE=2`.
- `REGULARIZATION`: finite `>=0`. Candidate is requested to use `REGULARIZATION=0.0`.

### Controlled variables
- `seed=0`.
- Training/holdout split: same seed, 8 training samples and 3 held-out samples, no overlap.
- Training-only input normalization.
- Dataset: `20260917T193218_folder_3d60b6ac4cc842a498b7e65534660f3d_dataset_6a49e7bd` / `dataset.json`.
- Solver: augmented least squares with reorthogonalized QR.
- Baseline remains immutable: `baseline/baseline.py` with `DEGREE=1`, `REGULARIZATION=0.01`.
- Only `candidate.py` may be modified.

### Dependent variables / measured metrics
- `baseline_mse`.
- `candidate_mse`.
- `improvement = baseline_mse - candidate_mse`.
- `candidate_terms`.

## 4. Metrics and decision rule

### Primary metric
- `candidate_mse`: lower is better.

### Secondary metrics
- `baseline_mse`: lower is better.
- `improvement`: higher is better; positive means candidate improved over baseline.
- `candidate_terms`: expected to equal `3` for `DEGREE=2` (intercept plus two polynomial coefficients).

### Decision rule
- **Success**: `candidate_mse < baseline_mse` (equivalently `improvement > 0`) **and** `candidate_terms == 3`.
- **Expected if hypothesis is correct**: `candidate_mse` close to `0`, `improvement` approximately equal to `baseline_mse`, and `candidate_terms == 3`.
- **Honest failure**: if `candidate_mse >= baseline_mse`, or validation fails, or the measured `candidate_terms` is not 3, report no improvement/failure without claiming success.

Because the exact seed-0 shuffle is not locally reproduced before execution, the concrete `baseline_mse` value is not known from this plan; it must be measured by the real local command.

## 5. Ablation matrix

Single representative ablation, as requested:

| Name | DEGREE | REGULARIZATION | seed | Purpose |
|---|---|---|---|---|
| `quadratic_ols_candidate_vs_linear_ridge_baseline` | 2 | 0.0 | 0 | Compare unregularized quadratic candidate against immutable linear ridge baseline |

If the evaluator rejects or numerically fails with `REGULARIZATION=0.0`, fall back to `REGULARIZATION=1e-9` inside the same ablation; this remains a near-OLS candidate and should not require a separate run unless the previous attempt fails deterministically.

## 6. Execution procedure

1. Read `baseline/baseline.py`, `candidate.py`, `data/dataset.json`, and relevant repository context before modification.
2. Preserve protected paths: `baseline/`, `data/`, `.mars/`, `diagnostics.yaml`, the evaluator, and the train/held-out split.
3. Modify only `candidate.py`: set `DEGREE=2` and `REGULARIZATION=0.0`.
4. Run the configured real local command for one experiment with `seed=0`; the evaluator independently fits both baseline and candidate on the same 8 training samples and evaluates on the same 3 held-out samples.
5. Record measured values for `baseline_mse`, `candidate_mse`, `improvement`, and `candidate_terms`.
6. Report the actual comparison, including no improvement or failed validation if that occurs.

## 7. Risks and mitigations

- **`REGULARIZATION=0.0` solver/validation issue**: fallback to `1e-9` for a near-OLS fit.
- **Overfitting or non-quadratic data**: the upstream proposal reports point-by-point verification that the full dataset satisfies the quadratic form, but execution must report actual metrics rather than assume the result.
- **No improvement**: report failure honestly; do not claim success without a measured positive `improvement`.

## 8. Budget

- `estimated_runs`: 1.
- `estimated_gpu_hours`: 0.0; this is a CPU ridge regression fixture and uses no GPU.