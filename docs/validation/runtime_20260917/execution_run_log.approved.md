---
schema: run_log.v1
project: folder_6dffbd8f7afc43dc99bc9d814673e3c9
agent: execution
run_id: planned_cpu_regression_seed0
status: interrupted
metrics:
  planned_experiments: 1
fingerprint_hash: sha256:7793a61ce95f1ed65921383e177bbbfbd78a18ab73f8388b9ed0db693e3b1866
planned_experiments:
- name: candidate_degree2_reg0
  config:
    seed: 0
execution_phase: planned
is_mock: false
---

# Execution Plan: CPU Polynomial Ridge Regression Fixture

## Status

- `execution_phase`: `planned`
- `status`: `interrupted`
- `is_mock`: `false`

This execution agent has **not** started the configured real local command and has **not** produced experiment measurements. No execution tools were used in this phase. No results are claimed.

## Planned experiment

The approved `experiment_plan.approved.md` and `code_spec.approved.md` specify exactly **one** ablation:

| Run name | Model | Candidate DEGREE | Candidate REGULARIZATION | Seed | Notes |
| --- | --- | --- | --- | --- | --- |
| `candidate_degree2_reg0` | `candidate.py` vs immutable `baseline/baseline.py` | `2` | `0.0` | `0` | Same 8 training / 3 held-out samples; training-only normalization |

The immutable baseline remains:

- `baseline/baseline.py`: `DEGREE = 1`, `REGULARIZATION = 0.01`

The planned candidate change is limited to:

- `candidate.py`: `DEGREE = 2`, `REGULARIZATION = 0.0`

No protected paths may be edited: `baseline/`, `data/`, `.mars/`, `diagnostics.yaml`, the evaluator, or the train/held-out split.

## Pending execution requirements

When execution is actually started by the bridge, the configured real local command must fit both real ridge models and emit request-bound metric evidence:

- `baseline_mse`
- `candidate_mse`
- `improvement`
- `candidate_terms`

The decision criterion is:

- Success: `improvement = baseline_mse - candidate_mse > 0`
- Otherwise: report no improvement or failed validation honestly

Do not claim success until the evaluator emits measured values. Do not claim PIMC relevance or scientific novelty.

## Metrics in this run_log

Only the number of planned experiments is reported here:

- `planned_experiments`: `1`

No duration, device, runtime, or evaluation metrics are reported because execution has not occurred.