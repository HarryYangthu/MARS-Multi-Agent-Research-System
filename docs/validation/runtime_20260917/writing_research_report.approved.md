---
schema: report.v1
project: folder_6dffbd8f7afc43dc99bc9d814673e3c9
agent: writing
deliverable_type: research_report
target_audience: phd_advisor
chain_refs:
  proposal: idea/idea_proposal.approved.md
  plan: experiment/experiment_plan.approved.md
  code: coding/code_spec.approved.md
  runs:
  - execution/run_log.approved.md
  - execution/metrics.json
  - execution/batch_summary.json
  - diagnosis/diagnosis.v1.md
---

# CPU 多项式岭回归 fixture 研究报告

## 摘要

本工程回归 fixture 仅允许修改 `candidate.py` 的 `DEGREE` 与 `REGULARIZATION`，目标是在 seed 0、同一 8 训练 / 3 留出划分、训练-only 归一化下，与不可变基线 `DEGREE=1, REGULARIZATION=0.01` 比较留出 MSE。

候选配置为 `DEGREE=2, REGULARIZATION=0.0`。根据 `execution/metrics.json` 的实测记录，留出 MSE 从基线的 `31.1103` 降至 `1.11755e-30`，`improvement = 31.1103 > 0`，`candidate_terms = 3`。因此在本实验的工程判据 `improvement > 0` 下，候选配置降低了留出 MSE。需要同时说明：`diagnosis.v1.md` 记录 commander 验收未通过，原因是未提供启用的项目级验收准则，而非实测指标本身失败。

本报告不声称 PIMC 相关性或科研新颖性。

## 方法

- 数据是打包的合成数值样本，非 PIMC 数据或物理实验。
- 两个模型使用相同 seed 0 和相同的互斥训练/留出样本，训练-only 归一化。
- 求解器使用 augmented least squares 与 reorthogonalized QR；所有多项式系数含截距都受到 ridge 正则化影响。
- `REGULARIZATION=0.0` 使岭回归退化为普通最小二乘。候选为 `DEGREE=2`，对应特征为截距、x、x²，`candidate_terms` 期望为 3。
- 仅 `candidate.py` 的 `DEGREE` 与 `REGULARIZATION` 发生改变；`baseline/`、`data/`、`.mars/`、`diagnostics.yaml`、evaluator 及 train/held-out 划分被保留。

## 实验设置

| 项目 | 设置 |
| --- | --- |
| seed | 0 |
| 训练 / 留出 | 8 / 3，无重叠 |
| 归一化 | 仅使用训练输入 |
| 基线 | `DEGREE=1`, `REGULARIZATION=0.01`（不可变） |
| 候选 | `DEGREE=2`, `REGULARIZATION=0.0` |
| 候选修改文件 | `candidate.py` |
| 执行次数 | 1 次 |

## 实测结果

`execution/metrics.json` 记录 1 行实测，`execution/batch_summary.json` 显示 `attempt: 1`、`total: 1`、`failures: 0`，实验为 `candidate_degree2_reg0`。

| 指标 | 实测值 | 说明 |
| --- | --- | --- |
| `baseline_mse` | `31.1103` | 基线 DEGREE=1, REG=0.01 |
| `candidate_mse` | `1.11755e-30` | 候选 DEGREE=2, REG=0.0，接近机器精度 |
| `improvement` | `31.1103` | `baseline_mse - candidate_mse` |
| `candidate_terms` | `3` | 截距 + x + x² |

## 结果解读与失败/验收状态

1. **工程判据**：proposal/plan 定义的采纳判据为 `improvement = baseline_mse - candidate_mse > 0`。实测 `31.1103 > 0`，因此在本实验定义内成立。
2. **候选 MSE**：`1.11755e-30` 接近机器精度，未出现 `REGULARIZATION=0.0` 引发的数值退化或验证失败。
3. **项目级验收**：`diagnosis.v1.md` 显示 `passed: false`，原因是 “Metric acceptance was not evaluated: no enabled project criteria were supplied”，即未提供启用的项目级验收准则。这不等于实测指标失败；但严格来说，项目级 validation/gate 尚未完成。
4. **结论边界**：已验证候选配置在此本地 fixture 上产生正 improvement 且 candidate_terms=3；未验证项目级验收门槛是否满足，因为门槛未配置。

## 失败与局限

- 本实验只运行一个 seed（seed 0），未做多 seed 稳健性检查。
- 没有启用的项目级验收准则，因此不能将 `improvement > 0` 的结果表述为通过项目级 gate。
- 本 fixture 为合成数据，结果不能外推到 PIMC 数据或真实物理实验。
- 未执行科学验证，也未声称科研新颖性。

## 证据来源

- `idea/idea_proposal.approved.md`：提出候选 `DEGREE=2, REGULARIZATION=0.0` 与判据 `improvement > 0`。
- `experiment/experiment_plan.approved.md`：定义单一 ablation、seed 0、8/3 划分、训练-only 归一化及指标。
- `coding/code_spec.approved.md`：记录实际 diff，仅修改 `candidate.py`。
- `execution/run_log.approved.md`：执行前计划阶段记录。
- `execution/metrics.json`、`execution/batch_summary.json`：实测指标与批次汇总。
- `diagnosis/diagnosis.v1.md`：commander 诊断，指出项目级验收准则缺失。

## 下一步

若需要正式项目级验收：

1. 为该项目提供启用的、可执行的验收准则（例如 `improvement > 0` 且 `candidate_terms == 3`），并重新运行 commander 验收。
2. 若验收只关心工程目标，本报告已将候选配置的实测 improvement 与 `candidate_terms` 记录在案；可以终止，不声称科研 novelty。
3. 若希望增强稳健性，可在后续任务中增加多 seed 运行，但本任务要求只运行 seed 0，因此不在本报告范围内扩展。