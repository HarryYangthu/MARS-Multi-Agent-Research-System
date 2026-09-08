---
schema: proposal.v1
project: pimc
agent: idea
created: '2026-09-08'
research_question: 在固定均匀16×16网格、256个可训练实参数约束下，如何通过替换插值核或重构参数化来提升二维实值LUT对局部高曲率/非均匀采样函数的表达能力？
hypothesis: 将2D LUT的插值核从双线性(4点模板)替换为网格无关的RBF参数化(253个RBF权重+3个线性多项式系数=256参数)可在不改变256参数预算的前提下提升对局部高曲率/非均匀采样函数的表达能力，因为RBF中心可非均匀布置以集中分辨率于高曲率区域。
novelty: 在固定256标量参数预算下，系统比较固定均匀16×16双线性网格与网格无关RBF(253权重+3线性系数)作为可学习2D LUT参数化的表达能力，给出参数计数、边界处理、初始化与判别实验的完整定义。
human_summary: 把二维LUT从固定均匀16×16双线性网格换成网格无关的RBF参数化(253个RBF权重+3个线性系数，共256参数)，因RBF中心可非均匀布置以匹配局部高曲率，可能表达更准；此改动是否带来真实提升需由判别实验验证。
handoff:
  version: idea.handoff.v1
  target_agent: experiment
  scope: method_proposal
  next_step: 按evaluation_protocol实现baseline(双线性核)与candidate(RBF参数化)两臂，在共享训练数据与目标下训练并比较held-out误差，按decision_rule判定。
  changes:
  - target: parameterization
    operation: replace
    spec_ref: /method_spec/parameterization
    preserve:
    - input_domain
    - parameter_count
    - output_semantics
  verification_requirements:
  - id: v1
    question: RBF参数化是否在held-out集上取得不劣于双线性网格的误差？
    comparison: candidate vs baseline在相同held-out评估数据上的均方误差
    metric: held_out_mse
    decision_rule_ref: /decision_rule
  required_context:
  - kind: baseline_code
    description: 公开符号基线：输入(x,y)∈[-1,1]^2、输出实标量、固定均匀16×16网格、256个可训练实参数、双线性插值的可运行实现
    reason: 作为对照臂与candidate共享训练数据/目标/优化器/初始化，需其真实代码
    blocks_execution: true
  - kind: data_description
    description: 用于训练与held-out评估的真实数据集的格式、采样分布与划分方式
    reason: 判别实验需真实数据；局部高曲率/非均匀采样仅为待验证假设，非已观察事实
    blocks_execution: true
method_spec:
  input_domain: 输入(x,y)∈[-1,1]^2，输出实标量f(x,y)
  grid_geometry: baseline为固定均匀16×16网格，节点坐标u_i=-1+2i/15, v_j=-1+2j/15, i,j∈{0,...,15}
  parameter_count: baseline与candidate均为256个可训练实参数
  parameterization:
    baseline: 双线性核：取(x,y)所在单元4个角点，f=Σ_{a,b∈{0,1}} w_{ab}·(1-|x-u|)(1-|y-v|)形式的双线性权重，256个网格参数W
    candidate: 网格无关RBF参数化：f(x,y)=Σ_{j=1..253} c_j·φ(‖(x,y)-ξ_j‖)+a_x·x+a_y·y+a_0，其中φ为高斯核φ(r)=exp(-(r/α)^2)，253个RBF权重c_j+3个线性系数(a_x,a_y,a_0)=256参数；中心ξ_j固定于非均匀布置(高曲率区域加密)或作为可训练参数
    boundary: RBF为全局核，在[-1,1]^2边界外需显式处理；线性多项式项P_1=a_x·x+a_y·y+a_0提供全局趋势再生，RBF项提供局部修正
    differentiability: RBF参数化对权重c_j可微；若中心ξ_j也训练则对ξ_j可微，可端到端反向传播
    conditioning: RBF系统通常病态(稠密矩阵)且对形状参数α敏感，需调α；论文用最小二乘/奇异值分解求解而非SGD，梯度训练动力学未在论文中建立
  initialization: c_j初始化为零或小随机值(如N(0,0.01))，线性系数初始化为零，两臂使用相同初始化种子
  training_objective: 在训练数据上最小化均方误差MSE=mean((f(x,y)-y_true)^2)
  optimizer: Adam，学习率固定(如1e-3)，两臂相同
  randomness: 数据划分与参数初始化由固定种子控制；跨种子重复时仅随机源(数据采样/初始化)变化
  evaluation: held-out集上计算MSE，按decision_rule比较
decision_rule:
  metric: held_out_mse
  direction: lower_is_better
  comparison: candidate vs baseline在相同held-out评估数据上的MSE
  resampling_unit: 独立随机种子(数据划分+初始化)
  accept: candidate的held_out_mse显著低于baseline(如相对降低≥5%)且跨种子一致
  reject: candidate的held_out_mse不优于baseline或提升不显著
  inconclusive: 差异在噪声范围内无法判定
related_literature:
- title: 'Radial basis function approximations: comparison and applications'
  url: https://arxiv.org/abs/1806.07705
testable_predictions:
- prediction: 在局部高曲率函数上，RBF参数化的held-out MSE不高于双线性网格
  metric: held_out_mse
  expected_direction: candidate≤baseline
  success_threshold: 相对降低≥5%
- prediction: 在平滑函数上两参数化误差接近，RBF不劣于双线性
  metric: held_out_mse
  expected_direction: candidate≈baseline
risk_register:
- risk: RBF系统病态且对形状参数α敏感，梯度训练可能不收敛
  severity: high
  mitigation: 固定α并做网格搜索；用线性多项式项稳定全局趋势；若中心训练不收敛则固定中心
- risk: RBF为全局核，边界外需显式处理，可能引入边界伪影
  severity: medium
  mitigation: 单独评估边界区域误差；线性多项式项提供边界外延
- risk: 局部高曲率/非均匀采样为待验证假设，非已观察事实
  severity: high
  mitigation: 判别实验需真实数据，不预设性能提升
research_links:
- delegation_id: 1d6f3119c34f44fc814a006f30528cd7
  insight_id: insight_rbf_formula
  method_spec_ref: /method_spec/parameterization/candidate
  adaptation_reason: RBF论文自身定义网格无关RBF近似f(x)=Σ c_j·φ(‖x-ξ_j‖)，权重c_j为唯一未知量，用最小二乘求解；迁移为256参数2D
    LUT时用253个RBF权重+3个线性系数，中心可非均匀布置以匹配局部高曲率。
- delegation_id: 1d6f3119c34f44fc814a006f30528cd7
  insight_id: insight_rbf_nonuniform_centers
  method_spec_ref: /method_spec/parameterization/candidate
  adaptation_reason: RBF论文指出参考点无需均匀网格，布置反映底层表面(如沿地形断裂线)可提升近似质量；这直接支持非均匀采样提升表达能力的假设，但论文未建立梯度学习中心收敛性，故中心训练列为待验证。
- delegation_id: 1d6f3119c34f44fc814a006f30528cd7
  insight_id: insight_rbf_polynomial_reproduction
  method_spec_ref: /method_spec/parameterization/candidate
  adaptation_reason: RBF论文给出精确参数预算：E²中线性多项式P_1=a^T x+a_0使系统为M个RBF权重+3个线性系数；用M=253+3=256恰好满足预算，得到可精确再生仿射趋势并加局部RBF修正的光滑可微2D映射。
evaluation_protocol:
  version: idea.evaluation.v1
  datasets:
  - id: train_data
    role: train
    spec_ref: /method_spec/training_objective
  - id: heldout_data
    role: test
    spec_ref: /method_spec/evaluation
  objectives:
    mse:
      spec_ref: /method_spec/training_objective
      data_refs:
      - train_data
  arms:
    baseline:
      training_data_refs:
      - train_data
      assessment_data_refs:
      - heldout_data
      objective_ref: /evaluation_protocol/objectives/mse
      optimizer_ref: /method_spec/optimizer
      initialization_ref: /method_spec/initialization
    candidate:
      training_data_refs:
      - train_data
      assessment_data_refs:
      - heldout_data
      objective_ref: /evaluation_protocol/objectives/mse
      optimizer_ref: /method_spec/optimizer
      initialization_ref: /method_spec/initialization
  randomness:
    seeds:
    - 1
    - 2
    - 3
    sources:
    - name: data_split_and_init
      spec_ref: /method_spec/randomness
  comparison:
    isolates_architecture: true
    differences_justification: 两臂共享训练数据、目标、优化器与初始化，唯一差异为参数化(双线性网格vs RBF)，故隔离架构差异
parameter_budget:
  unit: real_scalar
  variables:
    grid_n: 16
    M_rbf: 253
    poly_coeffs: 3
  baseline_formula: grid_n*grid_n
  baseline_parameters: 256
  baseline_components:
  - name: W_bilinear
    formula: grid_n*grid_n
    dtype: real
    shape:
    - 16
    - 16
  candidate_formula: M_rbf+poly_coeffs
  candidate_parameters: 256
  candidate_components:
  - name: c_rbf
    formula: M_rbf
    dtype: real
    shape:
    - 253
  - name: a_linear
    formula: poly_coeffs
    dtype: real
    shape:
    - 3
  evaluation_cases:
  - name: grid16_vs_rbf253
    variables:
      grid_n: 16
      M_rbf: 253
      poly_coeffs: 3
    baseline_parameters: 256
    candidate_parameters: 256
signal_contract:
  output: 实标量f(x,y)
  input: (x,y)∈[-1,1]^2
  parameterization: 双线性网格(256)或RBF(253权重+3线性系数=256)
  trainable: 256个实参数
  objective: MSE
alternatives:
- name: catmull_rom_bicubic
  feasible: true
  parameters: 256
  components:
  - name: W_catmull
    formula: grid_n*grid_n
    dtype: real
    shape:
    - 16
    - 16
  selection_reason: 双三次Catmull-Rom核C1连续、过数据点，但需边界扩展处理且论文证据不足，列为备选
- name: bilinear_baseline
  feasible: true
  parameters: 256
  components:
  - name: W_bilinear
    formula: grid_n*grid_n
    dtype: real
    shape:
    - 16
    - 16
  selection_reason: 当前公开符号基线，作为对照臂保留，用于隔离参数化差异
ablation_plan:
- name: parameterization_type
  description: 双线性网格vs RBF参数化，其余相同
- name: center_training
  description: 固定RBF中心vs训练RBF中心对表达能力的影响
- name: shape_parameter_alpha
  description: 不同高斯核形状参数α对病态性与误差的影响
- name: polynomial_term
  description: 含线性多项式项vs纯RBF(253权重+3参数改为256权重)对仿射再生与稳定性的影响
quality_warnings:
- 主机验证仅识别到1个被检索引用的来源(RBF论文arXiv:1806.07705)，未达到2个独立来源的全局证据下限；Conv2Warp与Instant NGP虽在委派报告中含read
  receipt，但未被主机计入related_literature检索来源。
- 局部高曲率/非均匀采样为待验证假设，非已观察事实；未提供真实数据与代码，未声称完成任何PIMC仿真或性能提升。
---

把二维LUT从固定均匀16×16双线性网格换成网格无关的RBF参数化(253个RBF权重+3个线性系数，共256参数)，因RBF中心可非均匀布置以匹配局部高曲率，可能表达更准；此改动是否带来真实提升需由判别实验验证。