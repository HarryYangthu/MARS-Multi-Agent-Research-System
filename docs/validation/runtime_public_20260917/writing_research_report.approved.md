---
schema: report.v1
project: folder_3d60b6ac4cc842a498b7e65534660f3d
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

# CPU 多项式岭回归工程夹具研究报告

## 摘要

本项目是一个固定的合成数值数据 CPU 多项式岭回归工程夹具，不是 PIMC 数据或物理实验。本轮仅修改 `candidate.py` 的 `DEGREE` 与 `REGULARIZATION`，目标是在 seed 0 下与不可变基线使用相同的 8 个训练样本和 3 个留出样本，降低留出集 MSE。

实测结果：

- 基线 `baseline/baseline.py`：`DEGREE=1`、`REGULARIZATION=0.01`，`baseline_mse=31.1103`。
- 候选 `candidate.py`：`DEGREE=2`、`REGULARIZATION=0.0`，`candidate_mse=1.11755e-30`。
- `improvement = baseline_mse - candidate_mse = 31.1103`，为正向改进。
- `candidate_terms=3`，与二次多项式模型期望的截距加两个多项式系数一致。

按预设决策规则，本次实验成功：`candidate_mse < baseline_mse` 且 `candidate_terms == 3`。该结论仅针对本工程夹具，不声称 PIMC 抵消能力或研究新颖性。

## 研究方法

- **任务类型**：CPU 实数岭回归工程夹具；仅允许修改 `candidate.py` 的 `DEGREE`（整数 1..5）与 `REGULARIZATION`（有限且 >=0）。
- **候选配置**：`DEGREE=2`、`REGULARIZATION=0.0`；这是对上游想法提议中“无噪声二次函数 `y = 1.5*x^2 - 0.75*x + 0.5`”假设的执行落地。
- **不可变基线**：`baseline/baseline.py` 保持 `DEGREE=1`、`REGULARIZATION=0.01`。
- **评估方式**：评估器执行真实 ridge 拟合；同一 seed 下两个模型独立拟合，训练样本数为 8，留出样本数为 3，二者不相交；输入归一化仅使用训练样本。
- **求解器**：增广最小二乘 + 重正交化 QR；所有多项式系数（含截距）都参与岭正则化。当 `REGULARIZATION=0.0` 时退化为普通最小二乘。
- **受保护路径**：`baseline/`、`data/`、`.mars/`、`diagnostics.yaml`、评估器和训练/留出划分均未修改。

## 实验设置

| 项目 | 设置 |
|---|---|
| 数据集 ID | `20260917T193218_folder_3d60b6ac4cc842a498b7e65534660f3d_dataset_6a49e7bd` |
| 数据文件 | `dataset.json` |
| checksum | `sha256:ac4a2f1fe65591e1cc0ca8add1944cdc03851223d67790af71ed82b6c44c8a90` |
| 样本点数 | 11 |
| seed | 0 |
| 训练 / 留出 | 8 / 3，不相交 |
| 归一化 | 仅使用训练样本 |
| 候选 | `DEGREE=2`、`REGULARIZATION=0.0` |
| 基线 | `DEGREE=1`、`REGULARIZATION=0.01` |
| 实验名 | `quadratic_ols_candidate_vs_linear_ridge_baseline` |

## 结果

实际执行指标来自 `execution/metrics.json`：

| 指标 | 实测值 |
|---|---:|
| `baseline_mse` | 31.1103 |
| `candidate_mse` | 1.11755e-30 |
| `improvement` | 31.1103 |
| `candidate_terms` | 3 |

`execution/batch_summary.json` 显示：attempt=1，total=1，max_concurrency=1，failures=0。`diagnosis/diagnosis.v1.md` 显示 passed=true，置信度为 1.0，无失败指标。

结论：候选二次 OLS 配置在本合成数值夹具上显著优于线性 ridge 基线；留出集 MSE 从 31.1103 降至数值零水平，改善量为 31.1103。

## 失败与风险分析

本次执行未发生验证失败或数值失败。上游计划中考虑到的 `REGULARIZATION=0.0` 求解器风险未触发，因此未使用 `1e-9` 回退。未发现需要报告的无改进情况。

## 局限性

- 数据为固定合成数值样本，只有 11 个点；结果不能泛化为 PIMC 数据、物理实验或一般统计建模任务。
- 本轮按指定方案只执行 seed 0 的一组实验，没有跨 seed 或跨候选配置的消融扫描。
- `candidate_mse` 接近数值零，支持“数据可由无噪声二次函数精确拟合”的工作假设，但这仍是单夹具内的工程配置验证，而非科学或算法新颖性声明。
- 训练/留出划分由评估器在 seed 0 下执行；本报告引用实际执行指标文件，未在写作阶段重新运行本地拟合命令。

## 下一步

当前配置已满足预设成功条件，无需回退到实验代理。若后续需要更强的复现性证据，可在不修改受保护路径的前提下扩展跨 seed 查看或对 `DEGREE`、`REGULARIZATION` 做受控扫描；但本任务要求保持 seed 0 的单次实验口径，因此本报告以上述实测结果为准。

## 引用链

- 提议：`idea/idea_proposal.approved.md`
- 实验计划：`experiment/experiment_plan.approved.md`
- 代码规格：`coding/code_spec.approved.md`
- 执行记录：`execution/run_log.approved.md`、`execution/metrics.json`、`execution/batch_summary.json`
- 诊断：`diagnosis/diagnosis.v1.md`
