---
schema: proposal.v1
project: folder_6dffbd8f7afc43dc99bc9d814673e3c9
agent: idea
research_question: 该确定性合成 fixture 的 y 是否是 x 的低阶多项式？当前 DEGREE=1 的线性基线是否因无法表示二次项而在留出集产生非零
  MSE，从而可通过把 candidate.py 的 DEGREE 调到 2、REGULARIZATION 调到 0 来降低留出 MSE？
hypothesis: 数据精确满足 y=1.5x^2-0.75x+0.5（纯二次、无噪声，11 点全匹配）。基线 DEGREE=1 无法表示二次项，留出 MSE
  非零；候选 DEGREE=2、REGULARIZATION=0 可在 8 个训练点上精确恢复该二次多项式，留出 MSE 降至接近机器精度，improvement
  = baseline_mse - candidate_mse > 0。
novelty: 无科研新颖性：这是工程回归 fixture，不是 PIMC 或科学创新。通过从实际数据逐点识别出精确二次生成函数，将阶数 1→2、正则化 0.01→0
  消除欠拟合以降低留出 MSE。
theoretical_basis: 对给定 11 个 x/y 点逐点验证 y=1.5x^2-0.75x+0.5 全点零残差（x=-2→8.0, -1.5→5.0,
  -1.0→2.75, -0.5→1.25, 0→0.5, 0.5→0.5, 1.0→1.25, 1.5→2.75, 2.0→5.0, 2.5→8.0, 3.0→11.75），全部精确匹配。训练/留出为同一无噪声二次函数；训练-only
  归一化是 x 的仿射变换，不改变二次函数空间，二次模型可无偏外推。REGULARIZATION=0 使岭回归退化为普通最小二乘（β̂=(XᵀX+αI)⁻¹Xᵀy
  在 α=0 时即 OLS），8 点过定拟合 3 参数数值稳定。
constraints:
- 仅可修改 candidate.py 的 DEGREE（整数 1..5）与 REGULARIZATION（有限 >=0）
- 不可修改 baseline/、data/、.mars/、diagnostics.yaml、evaluator、train/held-out 划分
- seed 0：8 个训练点 / 3 个留出点，训练-only 归一化
human_summary: 数据已逐点核对为精确无噪声二次函数 y=1.5x²−0.75x+0.5，基线 DEGREE=1 欠拟合；候选改为 DEGREE=2、REGULARIZATION=0
  可精确拟合并降低留出 MSE。
testable_predictions:
- prediction: candidate_mse 显著低于 baseline_mse，接近机器精度
  metric: held-out validation_mse
  expected_direction: decrease
  success_threshold: candidate_mse < baseline_mse
- prediction: improvement = baseline_mse - candidate_mse > 0
  metric: improvement
  expected_direction: increase
  success_threshold: improvement > 0
- prediction: candidate_terms = 3（截距 + x + x²）
  metric: candidate_terms
  expected_direction: equal
  success_threshold: == 3
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
  - 'candidate: DEGREE=2, REGULARIZATION=0 vs baseline: DEGREE=1, REGULARIZATION=0.01'
risk_register:
- risk: REGULARIZATION=0 下 augmented least squares 数值稳定性
  severity: low
  mitigation: evaluator 使用 reorthogonalized QR，8 点拟合 3 参数为过定最小二乘，数值稳定；若出现退化可回退到极小正正则化（如
    1e-9）但仍保证 improvement
- risk: 若无改善或验证失败
  severity: low
  mitigation: 诚实报告失败，不声称实验结果成功
handoff:
  version: idea.handoff.v1
  target_agent: experiment
  scope: project_proposal
  next_step: 用 seed 0 运行配置好的真实本地命令，分别拟合基线(DEGREE=1, REG=0.01)与候选(DEGREE=2, REG=0)的岭回归，报告
    baseline_mse、candidate_mse、improvement、candidate_terms，诚实报告有无改善或验证失败。
  changes:
  - target: candidate.py:DEGREE
    operation: replace
    spec_ref: /method_spec/polynomial_degree
    preserve:
    - baseline/baseline.py
    - data/dataset.json
    - train/holdout split
    - training-only normalization
  - target: candidate.py:REGULARIZATION
    operation: replace
    spec_ref: /method_spec/regularization
    preserve:
    - baseline/baseline.py
    - data/dataset.json
    - train/holdout split
    - training-only normalization
  verification_requirements:
  - id: heldout_mse_improvement
    question: 候选 DEGREE=2、REGULARIZATION=0 的留出 MSE 是否低于基线 DEGREE=1、REGULARIZATION=0.01？
    comparison: baseline_mse vs candidate_mse（seed 0，同一 8/3 划分）
    metric: held-out validation_mse
    decision_rule_ref: /decision_rule
  required_context:
  - kind: data_description
    description: data/dataset.json 的 11 个 x/y 点，已逐点核对为精确二次 y=1.5x^2-0.75x+0.5，无噪声
    reason: 确定最优阶数=2、正则化=0
    blocks_execution: false
  - kind: baseline_code
    description: 'baseline/baseline.py: DEGREE=1, REGULARIZATION=0.01（不可变）'
    reason: 作为对照基准
    blocks_execution: false
  - kind: metric_definition
    description: evaluator 用训练-only 归一化、augmented least squares + reorthogonalized
      QR，所有多项式系数含截距都受 ridge 正则化；validation_mse 在留出集计算
    reason: 确认 REGULARIZATION=0 时退化为普通最小二乘且数值稳定
    blocks_execution: false
method_spec:
  polynomial_degree: 2
  regularization: 0.0
  generative_function: y = 1.5*x^2 - 0.75*x + 0.5（精确、无噪声，11 点全匹配）
  fit_procedure: 训练-only 归一化 x 后构造含截距的多项式特征（次数=2），augmented least squares + reorthogonalized
    QR 拟合；REGULARIZATION=0 退化为普通最小二乘（β̂=(XᵀX+αI)⁻¹Xᵀy 在 α=0），8 个训练点过定拟合 3 个系数，精确恢复二次多项式
  expected_terms: 3
decision_rule:
  criterion: 若 improvement = baseline_mse - candidate_mse > 0（即 candidate_mse < baseline_mse）则采纳候选
    DEGREE=2、REGULARIZATION=0；否则诚实报告无改善或验证失败，不得声称实验成功
  expected_terms: 3
  baseline:
    degree: 1
    regularization: 0.01
  candidate:
    degree: 2
    regularization: 0.0
research_context:
  schema: idea.research_context.v1
  question: 如何仅通过调整 candidate.py 的 DEGREE 与 REGULARIZATION（不改 baseline、data、划分）降低留出
    MSE？
  selection_principles:
  - 读取实际 baseline.py、candidate.py、data/dataset.json 源码而非知识快照（证据记入 required_context，不当作文献来源）
  - 优先从数据本身逐点验证生成函数；确定性无噪声 fixture 可直接判定最优阶数，无需交叉验证类文献
  - 文献仅作一般背景（岭回归→OLS 的退化关系），不用于确定阶数；采用项必须来自实际抓取并读取正文的工具结果并复制其 source_id
  sources:
  - source_id: source_2a4c841d9ae44a32
    title: 'Fractional ridge regression: a fast, interpretable reparameterization
      of ridge regression'
    url: https://arxiv.org/pdf/2005.03220v1.pdf
    decision: use
    reason: 实际抓取并读取了正文（第 1-4 页，含 2.1 Background and theory），给出岭回归估计量 β̂RR=(XᵀX+αI)⁻¹Xᵀy，α=0
      时退化为普通最小二乘，为 REGULARIZATION=0 的数值与统计合理性提供依据
    method_sections:
    - 2.1 Background and theory
    method_summary: 岭回归在 OLS 目标上增加系数 L2 范数惩罚，估计量为 β̂RR=(XᵀX+αI)⁻¹Xᵀy；当 α=0 时该式退化为普通最小二乘解
      β̂OLS=(XᵀX)⁻¹Xᵀy。作者提出按 γ（正则化与无正则化系数 L2 范数之比）重参数化以自动覆盖 α 的取值区间。
    transfer: 支持本方案将 REGULARIZATION 设为 0：在无噪声、过定（8 点拟合 3 参数）的确定性二次数据上，α=0 的 OLS 解可精确恢复生成函数且无收缩偏置；evaluator
      的 reorthogonalized QR 保证数值稳定。
    limitations: 该文献针对大规模数据下 α 的自动选取，不直接涉及多项式阶数选择；α=0 退化为 OLS 的适用性依赖本 fixture 数据无噪声且过定，若数据含噪声则仍需重新权衡
      bias-variance。
    method_spec_ref: /method_spec/regularization
  - source_id: ''
    title: A survey of cross-validation procedures for model selection
    url: http://arxiv.org/abs/0907.4728
    decision: reject
    reason: 一般模型选择综述；本 fixture 为无噪声精确二次，无需 CV 即可直接确定阶数，不采用
  stop_reason: 数据已逐点核对为精确无噪声二次函数 y=1.5x^2-0.75x+0.5，最优候选 DEGREE=2、REGULARIZATION=0
    可直接确定；已实际读取一份用于佐证 α=0 退化关系的正文并记录 source_id，进一步文献检索不会改变结论，故停止。
  open_questions: []
---

数据已逐点核对为精确无噪声二次函数 y=1.5x²−0.75x+0.5，基线 DEGREE=1 欠拟合；候选改为 DEGREE=2、REGULARIZATION=0 可精确拟合并降低留出 MSE。