---
schema: proposal.v1
project: pimc
agent: idea
created: '2026-09-08T03:34:06Z'
research_question: 在二维实值查找表（LUT）中，固定均匀16×16网格、双线性插值、256个可训练实参数作为公开符号基线时，能否在不增加可训练参数总数（≤256）的前提下，通过把部分参数预算从节点值重新分配到可训练节点坐标，提升对局部高曲率函数的表达能力？
hypothesis: 把256个可训练实参数从'全部为节点值'重新分配为'较少节点值+可训练节点坐标'，使节点在输入域内自适应聚集到高曲率区域，可在相同参数预算下降低双线性插值对局部高曲率函数的逼近误差。此假设为待验证命题，非已观察事实。
novelty: 在固定参数预算约束下，将均匀网格LUT的节点坐标从固定常量改为可训练参数，形成'可变形双线性LUT'，使插值权重与节点值同时依赖可训练坐标。该设计在公开符号基线基础上做单一最小改动，且保持总可训练实参数≤256。
theoretical_basis: 双线性插值在均匀网格上的逼近误差与局部二阶导数（曲率）及网格间距相关；在固定节点数下，非均匀分布节点可降低局部网格间距从而减小局部插值误差，但代价是其他区域网格间距增大。此为待验证的推理，非已证定理。
constraints:
- 候选总可训练实参数≤256
- 输入(x,y)∈[-1,1]^2，输出实标量
- 保持双线性插值语义（每点由所在四边形四角节点值加权）
- 局部高曲率与不均匀采样仅为待验证瓶颈假设，非已观察事实
- 未提供真实数据与代码，不得声称项目就绪或已测得性能
related_literature: []
testable_predictions:
- prediction: 在含局部高曲率区域的测试函数上，可变形双线性LUT的均方误差低于均匀网格基线
  metric: 测试集均方误差
  expected_direction: 降低
  success_threshold: 见decision_rule
- prediction: 在平滑低曲率测试函数上，可变形双线性LUT不劣于均匀基线（坐标自由度不带来系统性退化）
  metric: 测试集均方误差
  expected_direction: 不劣于
  success_threshold: 见decision_rule
experiment_hint:
  variables:
  - 可训练节点坐标
  - 节点值
  metrics:
  - 测试集均方误差
  minimal_ablations:
  - 固定坐标（退化为基线）
  - 仅训练坐标不训练值
  - 坐标正则化强度
evidence_refs: []
risk_register:
- risk: 可训练坐标导致插值权重非凸、优化不稳定或坐标折叠（节点重叠）
  severity: high
  mitigation: 坐标单调性约束与重叠惩罚正则项；初始化于均匀网格
- risk: 坐标自由度挤占节点值预算，导致整体表达下降
  severity: medium
  mitigation: 参数预算对比实验与消融
- risk: 无真实数据与代码，无法验证瓶颈假设
  severity: high
  mitigation: 在method_spec中定义符号测试函数集，交由Experiment agent实现
- risk: 文献证据缺失（研究委派预算耗尽、KB为空）
  severity: high
  mitigation: 本提案不引用任何未读论文，明确标注证据缺口
downstream_requirements:
- 真实数据描述
- 基线代码
- 训练目标与优化器实现
- 随机源实现
research_artifacts:
  kb_queries: 2
  kb_hits: 0
  delegation_attempts: 3
  delegation_success: false
  verified_papers_read: 0
  evidence_status: no verified literature evidence obtained; research delegation budget
    exhausted and KB returned empty
quality_warnings:
- 未获得任何经核实的论文PDF页段，related_literature为空
- 研究委派预算耗尽，无法满足min_sources=2与min_pdfs=2的宿主要求
- 所有方法设计为Agent自身推理，非文献迁移，须由Experiment agent独立验证
- 局部高曲率瓶颈为待验证假设，非已观察事实
debate_summary:
  rounds: 0
  consensus: ''
  disagreements: []
  risks:
  - 无文献证据支撑方法迁移
  evidence_gaps:
  - 可训练坐标网格在2D双线性LUT上的方法学证据缺失
human_summary: 把均匀16×16网格LUT的256个可训练参数从全部是节点值，改为一部分节点值加一部分可训练节点坐标，让节点在输入域内自适应聚集到高曲率区域，期望在相同参数预算下降低局部高曲率函数的插值误差；此为待验证假设，非已测得提升。
handoff:
  version: idea.handoff.v1
  target_agent: experiment
  scope: method_proposal
  next_step: 实现method_spec中定义的可变形双线性LUT与均匀网格基线，在符号测试函数集上按evaluation_protocol运行受控对比，报告测试集均方误差并依decision_rule判定。
  changes:
  - target: method_spec
    operation: add
    spec_ref: /method_spec/architecture
    preserve: []
  - target: method_spec
    operation: add
    spec_ref: /method_spec/objective
    preserve: []
  - target: method_spec
    operation: add
    spec_ref: /method_spec/optimizer
    preserve: []
  - target: method_spec
    operation: add
    spec_ref: /method_spec/initialization
    preserve: []
  - target: method_spec
    operation: add
    spec_ref: /method_spec/randomness
    preserve: []
  - target: method_spec
    operation: add
    spec_ref: /method_spec/split_construction
    preserve: []
  - target: method_spec
    operation: add
    spec_ref: /method_spec/boundary_handling
    preserve: []
  verification_requirements:
  - id: vr1
    question: 可变形双线性LUT在含局部高曲率测试函数上的测试均方误差是否显著低于均匀网格基线？
    comparison: candidate vs baseline，同训练数据、同目标、同优化器、同初始化种子
    metric: 测试集均方误差
    decision_rule_ref: /decision_rule
  - id: vr2
    question: 在平滑低曲率测试函数上，可变形LUT是否不劣于基线？
    comparison: candidate vs baseline
    metric: 测试集均方误差
    decision_rule_ref: /decision_rule
  required_context:
  - kind: baseline_code
    description: 均匀16×16网格双线性插值LUT的可运行实现，含256个可训练节点值
    reason: 作为受控对比的基线臂，需实际可运行代码
    blocks_execution: true
  - kind: data_description
    description: 真实或符号测试函数的输入采样分布、目标函数定义与划分方式
    reason: 训练/验证/测试划分与目标函数定义依赖实际数据描述
    blocks_execution: true
  - kind: execution_environment
    description: 可运行自动微分训练的计算环境与随机源实现
    reason: 训练与随机种子重复需要实际执行环境
    blocks_execution: true
method_spec:
  architecture:
    baseline: 均匀16×16网格，节点坐标固定为x_i=-1+2i/15, y_j=-1+2j/15 (i,j=0..15)，256个可训练节点值v[i,j]∈R。对输入(x,y)，定位所在单元，双线性插值输出。
    candidate: 可变形双线性LUT：节点值个数N_v与可训练坐标个数N_c之和≤256。采用N=15×15=225个节点值（15×15网格）加N_c=31个可训练坐标参数，总参数=225+31=256。坐标参数定义：x方向15个内部可训练坐标t_k∈(-1,1)（k=1..15，含边界固定为±1的端点），y方向16个内部可训练坐标s_l∈(-1,1)（l=1..16）。实际节点位置为排序后的坐标。节点值v[i,j]定义在15×15节点上。
    interpolation: 对输入(x,y)，在x方向找到相邻可训练坐标t_a≤x≤t_{a+1}，y方向找到相邻s_b≤y≤s_{b+1}，双线性插值：out
      = (1-α)(1-β)v[a,b]+α(1-β)v[a+1,b]+(1-α)βv[a,b+1]+αβv[a+1,b+1]，其中α=(x-t_a)/(t_{a+1}-t_a)，β=(y-s_b)/(s_{b+1}-s_b)。坐标可训练使α、β及单元定位均依赖可训练参数，梯度经自动微分回传。
    parameter_count: baseline=256节点值；candidate=225节点值+31坐标=256。
    boundary_handling: 输入域[-1,1]^2，边界节点坐标固定为±1（不训练），保证插值覆盖整个输入域；域外输入不做外推（若出现则截断到边界）。
    trainable_quantities: 'baseline: v[i,j]共256个。candidate: v[i,j]共225个 + 内部x坐标15个
      + 内部y坐标16个 = 256个。'
    fixed_quantities: 网格拓扑（每点四邻接四边形）、边界坐标±1、插值公式、输入域[-1,1]^2。
  objective: 训练目标为在训练集上最小化均方误差：L=(1/|D_train|)Σ_{(x,y,z)∈D_train}(f_LUT(x,y)-z)^2。
  optimizer: Adam，学习率1e-3，对节点值与坐标参数使用相同优化器；坐标参数额外施加单调性保持（排序后使用）与重叠惩罚。
  initialization: 节点值v[i,j]初始化为0；坐标参数初始化为均匀网格位置（即t_k=-1+2k/16, s_l=-1+2l/16），使候选初始状态与基线一致。
  randomness: 随机源为训练数据划分的随机打乱与Adam的随机初始化（若使用）；种子集合见evaluation_protocol.randomness.seeds。
  split_construction: 对每个符号测试函数，在[-1,1]^2上按指定分布采样，随机划分为train/validation/test三份，划分由种子决定。
  boundary_handling_spec: 见architecture.boundary_handling。
decision_rule:
  metric: 测试集均方误差（越低越好）
  direction: lower_is_better
  comparison: candidate vs baseline，同训练数据、同目标、同优化器、同初始化种子，仅架构不同
  resampling_unit: 独立随机种子（每个种子重新采样数据并重训）
  accept: 在含局部高曲率测试函数上，candidate测试均方误差显著低于baseline（配对检验p<0.05且相对降幅≥10%），且在平滑低曲率函数上candidate不显著劣于baseline（相对增幅<5%）
  reject: candidate在含高曲率函数上未显著低于baseline，或在平滑函数上显著劣于baseline（相对增幅≥5%）
  inconclusive: 差异不显著或样本量不足以判定
evaluation_protocol:
  version: idea.evaluation.v1
  datasets:
  - id: high_curvature_fn
    role: test
    spec_ref: /method_spec/objective
  - id: smooth_fn
    role: test
    spec_ref: /method_spec/objective
  objectives:
    mse:
      spec_ref: /method_spec/objective
      data_refs:
      - high_curvature_fn
      - smooth_fn
  arms:
    baseline:
      training_data_refs:
      - high_curvature_fn
      - smooth_fn
      assessment_data_refs:
      - high_curvature_fn
      - smooth_fn
      objective_ref: /evaluation_protocol/objectives/mse
      optimizer_ref: /method_spec/optimizer
      initialization_ref: /method_spec/initialization
    candidate:
      training_data_refs:
      - high_curvature_fn
      - smooth_fn
      assessment_data_refs:
      - high_curvature_fn
      - smooth_fn
      objective_ref: /evaluation_protocol/objectives/mse
      optimizer_ref: /method_spec/optimizer
      initialization_ref: /method_spec/initialization
  randomness:
    seeds:
    - 1
    - 2
    - 3
    - 4
    - 5
    sources:
    - name: data_split_shuffle
      spec_ref: /method_spec/randomness
    - name: optimizer_state_init
      spec_ref: /method_spec/randomness
  comparison:
    isolates_architecture: true
    differences_justification: 两臂共享训练数据、目标函数、优化器与初始化种子，唯一差异为架构（均匀固定坐标 vs 可训练坐标），故隔离架构效应。
parameter_budget:
  unit: real_scalar
  variables:
    Nv_baseline: 256
    Nv_cand: 225
    Nc_x: 15
    Nc_y: 16
    Nc_total: 31
  baseline_formula: Nv_baseline = 256
  baseline_parameters: 256
  baseline_components:
  - name: node_values
    formula: Nv_baseline
    dtype: real
    shape:
    - 16
    - 16
  candidate_formula: Nv_cand + Nc_x + Nc_y = 225 + 15 + 16 = 256
  candidate_parameters: 256
  candidate_components:
  - name: node_values
    formula: Nv_cand
    dtype: real
    shape:
    - 15
    - 15
  - name: x_coords
    formula: Nc_x
    dtype: real
    shape:
    - 15
  - name: y_coords
    formula: Nc_y
    dtype: real
    shape:
    - 16
  evaluation_cases:
  - name: high_curvature_fn
    variables:
      Nv_baseline: 256
      Nv_cand: 225
      Nc_x: 15
      Nc_y: 16
      Nc_total: 31
    baseline_parameters: 256
    candidate_parameters: 256
  - name: smooth_fn
    variables:
      Nv_baseline: 256
      Nv_cand: 225
      Nc_x: 15
      Nc_y: 16
      Nc_total: 31
    baseline_parameters: 256
    candidate_parameters: 256
signal_contract:
  input: (x,y)∈[-1,1]^2，实标量
  output: 实标量f_LUT(x,y)
  phase_semantics: 训练阶段节点值与坐标均更新；评估阶段固定参数仅前向插值
  trainable: 见method_spec.architecture.trainable_quantities
  fixed: 见method_spec.architecture.fixed_quantities
alternatives:
- name: alt1_fixed_coords_more_values
  feasible: true
  parameters: 256
  components:
  - name: node_values
    formula: '256'
    dtype: real
    shape:
    - 16
    - 16
  selection_reason: 即基线，作为对照；不改变坐标故不检验坐标自适应假设
- name: alt2_trainable_coords_fewer_values
  feasible: true
  parameters: 256
  components:
  - name: node_values
    formula: '225'
    dtype: real
    shape:
    - 15
    - 15
  - name: x_coords
    formula: '15'
    dtype: real
    shape:
    - 15
  - name: y_coords
    formula: '16'
    dtype: real
    shape:
    - 16
  selection_reason: 主候选，检验坐标自适应假设
- name: alt3_more_values_fixed_coords_17x15
  feasible: true
  parameters: 255
  components:
  - name: node_values
    formula: '255'
    dtype: real
    shape:
    - 17
    - 15
  selection_reason: 对照：用相同预算增加节点值但不训练坐标，隔离'更多节点值'与'坐标自适应'的贡献
ablation_plan:
- name: abl1_fixed_coords
  description: 候选坐标固定为均匀位置不训练，仅训练225个节点值，检验坐标自由度贡献
  behavior_change: 坐标不再随训练移动，插值权重退化为均匀网格，行为在评估域上改变
  gradient_effect: 坐标参数无梯度更新，仅225个节点值经MSE损失梯度更新
- name: abl2_train_coords_only
  description: 仅训练坐标，节点值固定为0，检验坐标单独能否表达目标
  behavior_change: 节点值固定，仅坐标移动改变插值权重，行为在评估域上改变
  gradient_effect: 节点值无梯度，仅31个坐标参数经MSE损失经插值权重梯度更新
- name: abl3_no_overlap_penalty
  description: 移除坐标重叠惩罚正则，检验正则对稳定性的作用
  behavior_change: 坐标可自由重叠，插值单元可能退化，行为在评估域上改变
  gradient_effect: 坐标梯度不再含正则项，仅含MSE项，节点值梯度不变
---

把均匀16×16网格LUT的256个可训练参数从全部是节点值，改为一部分节点值加一部分可训练节点坐标，让节点在输入域内自适应聚集到高曲率区域，期望在相同参数预算下降低局部高曲率函数的插值误差；此为待验证假设，非已测得提升。