---
schema: run_log.v1
project: folder_3d60b6ac4cc842a498b7e65534660f3d
agent: execution
run_id: exec-plan-folder_3d60b6ac4cc842a498b7e65534660f3d-seed0
status: interrupted
metrics:
  planned_experiments: 1
fingerprint_hash: sha256:ff00d9ac96312376fa0831cba79bb44e4cefaeecf3b8822ffc16ad0f0ea3cd84
planned_experiments:
- name: quadratic_ols_candidate_vs_linear_ridge_baseline
  config:
    seed: 0
execution_phase: planned
is_mock: false
upstream_artifact: coding/code_spec.approved.md
---

# Execution Plan — CPU Regression Fixture

## 状态

本阶段仅生成真实执行计划，不调用执行工具，不声称已有实验结果。`execution_phase=planned`，`status=interrupted`。实际执行将在计划获批后由 bridge 启动；本报告没有可引用的本地执行收据，因此不提供耗时、设备或任何实测指标。

## 待执行实验

共安排 1 组实验，保留方案明确指定的 `seed=0`：

| 实验名 | seed | 目的 |
|---|---|---|
| `quadratic_ols_candidate_vs_linear_ridge_baseline` | 0 | 按已批准计划，在真实本地命令中比较候选与不可变基线，并回报 `baseline_mse`、`candidate_mse`、`improvement`、`candidate_terms` |

## 约束

- 仅允许修改 `candidate.py` 的 `DEGREE` 与 `REGULARIZATION`。
- `DEGREE` 必须为 1..5 的整数；`REGULARIZATION` 必须为有限且 >=0。
- 不修改 `baseline/`、`data/`、`.mars/`、`diagnostics.yaml`、评估器或训练/留出划分。
- 两个模型使用相同 `seed=0`，使用不相交的 8 个训练样本与 3 个留出样本，训练样本归一化。
- 本阶段只做计划，不执行本地命令、不读取执行日志、不采集实验指标。

## 预期执行证据

计划获批并由 bridge 实际执行后，应采集并报告：

- `baseline_mse`
- `candidate_mse`
- `improvement = baseline_mse - candidate_mse`
- `candidate_terms`

## 诚实报告要求

若实际执行后 `candidate_mse >= baseline_mse`、验证失败、`candidate_terms` 不为预期值，或真实本地命令不可用，必须如实报告无改进或失败，不得声称成功。