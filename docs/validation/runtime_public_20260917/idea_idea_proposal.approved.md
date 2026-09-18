---
schema: proposal.v1
project: folder_3d60b6ac4cc842a498b7e65534660f3d
agent: idea
research_question: 在固定合成数值数据上，能否通过调整多项式次数与岭正则化参数，使 held-out MSE 低于 DEGREE=1 的固定基线？
hypothesis: 数据精确满足二次函数 y=1.5x²−0.75x+0.5（无噪声，11 点逐一代入验证），因此候选 DEGREE=2、REGULARIZATION=0
  可精确拟合该函数，held-out MSE 趋近 0，显著低于线性基线。
novelty: 相对 DEGREE=1、REGULARIZATION=0.01 的固定基线，候选仅将多项式次数升为 2 并把正则化降为 0，属工程配置调优，不声称科学或
  PIMC 新颖性。
theoretical_basis: 岭回归讲义 §1.5 表明 λ>0 时有偏 E[β̂(λ)]=β−λ(XᵀX+λI)⁻¹β，且 lim_{λ↓0}β̂(λ)
  收敛于最小二乘估计；本数据为无噪声二次函数、设计矩阵良态（8 训练样本 > 3 系数、x 均匀），故取 λ=0 消除有偏性并唯一恢复二次系数。
constraints:
- 仅允许修改 candidate.py 的 DEGREE（1..5）与 REGULARIZATION（有限 >=0）
- 不得修改 baseline/、data/、评估器或训练/测试划分
- 两模型同 seed、同 8 训练/3 测试样本，训练样本归一化
related_literature:
- title: Lecture notes on ridge regression
  url: https://arxiv.org/pdf/1509.09169v9.pdf
- title: A survey of cross-validation procedures for model selection
  url: http://arxiv.org/abs/0907.4728
testable_predictions:
- prediction: candidate_mse 接近 0（精确拟合无噪声二次函数）
  metric: held-out MSE
  expected_direction: 约等于 0
  success_threshold: candidate_mse < baseline_mse
- prediction: candidate_terms 等于 3
  metric: model_terms
  expected_direction: = 3
  success_threshold: = 3
experiment_hint:
  variables:
  - DEGREE
  - REGULARIZATION
  metrics:
  - baseline_mse
  - candidate_mse
  - improvement
  - candidate_terms
  minimal_ablations:
  - DEGREE=2, REGULARIZATION=0 对照基线 DEGREE=1, REGULARIZATION=0.01
evidence_refs:
- ref: data/dataset.json
  kind: project_data
  summary: 11 个样本逐一代入 y=1.5x²−0.75x+0.5 完全吻合，为无噪声二次数据
risk_register:
- risk: REGULARIZATION=0 可能触发评估器增广最小二乘求解器的数值或验证问题
  severity: low
  mitigation: 改用极小正值（如 1e-9）仍近似精确拟合
- risk: DEGREE=2 若数据实际非二次则过拟合
  severity: low
  mitigation: 已逐点验证全部 11 个样本精确满足二次函数
downstream_requirements:
- 实验代理在 seed 0 下用配置的本地命令实测并回报 baseline_mse、candidate_mse、improvement、candidate_terms；无改进或验证失败时如实报告
human_summary: 数据为无噪声二次函数 y=1.5x²−0.75x+0.5（11 点逐一代入验证），候选改为 DEGREE=2、REGULARIZATION=0
  可精确拟合，held-out MSE 应趋近 0 并显著低于线性基线；请实验代理用 seed 0 实测 baseline_mse、candidate_mse、improvement
  与 candidate_terms，无改进则如实报告。
handoff:
  version: idea.handoff.v1
  target_agent: experiment
  scope: project_proposal
  next_step: 将 candidate.py 改为 DEGREE=2、REGULARIZATION=0，用 seed 0 跑一次实验（8 训练/3 测试），回报
    baseline_mse、candidate_mse、improvement 与 candidate_terms；若无改进或验证失败则如实报告。
  changes:
  - target: candidate.py
    operation: modify
    spec_ref: /method_spec/candidate_config
    preserve:
    - baseline/baseline.py
    - data/dataset.json
    - 训练/测试划分
    - 评估器
  verification_requirements:
  - id: heldout_mse
    question: 候选 DEGREE=2、REGULARIZATION=0 的 held-out MSE 是否低于 DEGREE=1 基线？
    comparison: candidate_mse 与 baseline_mse（同 seed 0、同 8 训练/3 测试划分）
    metric: held-out MSE
    decision_rule_ref: /decision_rule
  - id: terms
    question: 候选实际拟合系数个数是否为 3？
    comparison: candidate_terms 与预期值 3 比较
    metric: model_terms
    decision_rule_ref: /decision_rule
  required_context:
  - kind: data_description
    description: dataset.json 中 11 个样本精确满足 y=1.5x²−0.75x+0.5
    reason: 支撑 DEGREE=2、REGULARIZATION=0 的选择
    blocks_execution: false
  - kind: baseline_code
    description: baseline.py 与 candidate.py 当前均为 DEGREE=1、REGULARIZATION=0.01
    reason: 明确基线对比对象
    blocks_execution: false
method_spec:
  candidate_config:
    target_file: candidate.py
    degree: 2
    regularization: 0.0
    justification: 数据精确满足 y=1.5x²−0.75x+0.5（无噪声二次）；仿射归一化不改变多项式次数，DEGREE=2、REGULARIZATION=0
      的普通最小二乘可唯一恢复该二次函数，held-out MSE 理论为零。
  fit_procedure:
    normalization: 仅用训练样本计算均值与标准差（训练样本仿射归一化），由评估器实现
    basis: 标准化输入 z 的多项式基 [1, z, z²]
    solver: 增广最小二乘 + 重正交 QR，λ=0 时退化为普通最小二乘
    regularization_scope: 所有多项式系数（含截距）参与岭正则化；λ=0 时无惩罚
  degeneracy_handling:
    lambda_zero: λ=0 时增广行消失，退化为标准最小二乘；8 训练样本 > 3 系数，且 x 均匀分布、基矩阵良态
    fallback: 若评估器对 λ=0 报错或数值异常，改用极小正值 1e-9，拟合仍近似精确
decision_rule:
  success: candidate_mse < baseline_mse（improvement > 0）且 candidate_terms = 3
  expected: candidate_mse ≈ 0，improvement ≈ baseline_mse > 0
  report_failure: 若 candidate_mse >= baseline_mse 或验证失败，如实报告无改进/失败，不声称成功
  metric_direction: MSE 越低越好；improvement = baseline_mse - candidate_mse，越大越好
research_context:
  schema: idea.research_context.v1
  question: 固定合成数值数据上的最优多项式岭回归配置（DEGREE、REGULARIZATION）是什么？
  selection_principles:
  - 优先直接读取并逐点验证真实数据与源码，而非依赖摘要
  - 以解析验证（逐点代入）确定数据生成函数，避免无谓文献检索
  - 仅引用已实际读到方法文本的岭回归理论来源作为正则化/次数选择的依据
  sources:
  - source_id: source_756ad86890a8ab47
    title: Lecture notes on ridge regression
    url: https://arxiv.org/pdf/1509.09169v9.pdf
    decision: use
    reason: 已实际读到 §1.3–§1.5 的方法文本（pages 11–15），给出岭估计量定义、奇异值收缩与有偏性/λ→0 极限，直接支撑 REGULARIZATION=0
      的选择。
    method_sections:
    - 1.3 The ridge regression estimator
    - 1.4 Eigenvalue shrinkage
    - 1.5 Moments
    method_summary: §1.3 给出岭估计量 β̂(λ)=(XᵀX+λI)⁻¹XᵀY（Hoerl & Kennard 1970），λ∈(0,∞)；§1.4
      通过 SVD 说明岭回归把奇异值 d 收缩为 d/(d²+λ)；§1.5 给出期望 E[β̂(λ)]=β−λ(XᵀX+λI)⁻¹β，即 λ>0 时有偏，且
      lim_{λ↓0}β̂(λ) 收敛于最小二乘估计（无偏）。
    transfer: 该理论支持把 REGULARIZATION 设为 0（λ↓0 极限 = 普通最小二乘）：数据为无噪声二次函数、设计矩阵良态（8 训练样本
      > 3 系数、x 均匀），无需岭收缩，λ=0 消除有偏性并可唯一恢复二次系数。
    limitations: 讲义假设线性模型含噪声 ε~N(0,σ²)，本 fixture 为无噪声二次函数；讲义动机是共线性/高维，本数据低维且良态，故取
      λ↓0 的 OLS 极限而非正 λ。λ=0 严格不在讲义定义域 (0,∞)，但讲义明确指出 lim_{λ↓0} 收敛于最小二乘估计。
    method_spec_ref: /method_spec/fit_procedure
  - source_id: source_fb52bfeee748e496
    title: A survey of cross-validation procedures for model selection
    url: http://arxiv.org/abs/0907.4728
    decision: reject
    reason: PDF 文本提取乱码，无法作为方法证据；且本 fixture 数据为无噪声二次函数，解析验证已直接确定最优配置，无需交叉验证模型选择。
  stop_reason: 数据可解析验证为无噪声二次函数，候选 DEGREE=2、REGULARIZATION=0 的最优性由逐点代入确定，并已实际读到岭回归讲义
    §1.3–§1.5 的 λ→0 无偏极限作为方法支撑，继续检索不改变结论。
  open_questions:
  - seed 0 下的确切洗牌顺序未在本地复现，baseline_mse 的具体数值待实验代理实测；但不影响 candidate_mse≈0 的结论。
  - 评估器增广最小二乘求解器对 REGULARIZATION=0 的数值行为待实测确认；若报错可用极小正值替代。
---

数据为无噪声二次函数 y=1.5x²−0.75x+0.5（11 点逐一代入验证），候选改为 DEGREE=2、REGULARIZATION=0 可精确拟合，held-out MSE 应趋近 0 并显著低于线性基线；请实验代理用 seed 0 实测 baseline_mse、candidate_mse、improvement 与 candidate_terms，无改进则如实报告。