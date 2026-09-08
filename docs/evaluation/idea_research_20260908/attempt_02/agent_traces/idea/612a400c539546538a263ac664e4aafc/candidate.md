---
schema: proposal.v1
project: pimc
agent: idea
created: '2026-09-08T00:00:00Z'
research_question: 在总可训练实参数不超过256的约束下，能否通过把二维实值LUT的均匀16×16网格改为可训练的非均匀张量积网格（保持双线性插值）来提升对局部高曲率函数的表达能力？
hypothesis: 若目标函数在[-1,1]^2上存在空间非均匀的曲率，则把固定均匀网格的节点坐标改为可训练（在保持双线性插值的前提下）能让节点密度自适应地集中到高曲率区域，从而在相同或更少的参数预算下降低插值误差；若目标函数曲率近似均匀，则该改动不带来增益甚至因坐标优化困难而略差。
novelty: 在固定256实参数预算下，把二维LUT的均匀网格坐标本身作为可训练量（而非仅训练节点值），并用单调性保证的累积softplus间隙参数化，使非均匀采样成为可端到端优化的自由度。这是对'仅训练节点值'基线的单一最小改动，直接检验非均匀采样是否为表达能力瓶颈。
theoretical_basis: 双线性插值在每个单元内是双线性（每变量次数≤1），无法表达单元内曲率；单元内逼近误差随单元尺寸平方与二阶导数（曲率）乘积增长。固定均匀网格使所有单元等大，高曲率区域主导误差。把节点坐标可训练化允许高曲率区域获得更小单元。此为数学推理，非文献结论。
constraints:
- 总可训练实参数≤256
- 输入(x,y)∈[-1,1]^2，输出实标量
- 保持双线性插值语义
- 候选相对基线为单一最小改动
related_literature: []
testable_predictions:
- prediction: 在曲率空间非均匀的目标函数上，候选（可训练非均匀网格）的测试均方误差低于基线（均匀网格），因为节点密度可集中到高曲率区。
  metric: 测试集均方误差
  expected_direction: 候选<基线
  success_threshold: 候选测试MSE比基线低至少10%
- prediction: 在曲率近似均匀的目标函数（如平滑双线性或低阶多项式）上，候选不显著优于基线，因坐标自由度无利可图且增加优化难度。
  metric: 测试集均方误差
  expected_direction: 候选≈基线
  success_threshold: 两者差距在噪声范围内
experiment_hint:
  variables:
  - 目标函数曲率空间分布
  - 网格节点数n
  - 坐标学习率
  metrics:
  - 测试集均方误差
  - 训练收敛步数
  minimal_ablations:
  - 固定坐标候选(退化为均匀网格)
  - 去掉坐标梯度(仅值可训练)
  - 不同n取值
evidence_refs: []
risk_register:
- risk: 坐标可训练导致单元退化或节点交叉，破坏双线性插值良定义
  severity: high
  mitigation: 用累积softplus间隙参数化保证严格单调，端点固定于±1
- risk: 坐标梯度通过网格查找传播困难，优化不稳定
  severity: medium
  mitigation: 坐标与值分设学习率，坐标用较小学习率；用可微的单元定位
- risk: 文献证据缺失：三次委派均validation_exhausted，KB无命中，无已读PDF
  severity: high
  mitigation: 本方案基于双线性插值的数学性质推理，明确标注为推理而非文献结论；需后续补真实文献调研
- risk: 非均匀采样增益依赖曲率空间非均匀这一未验证假设
  severity: medium
  mitigation: 判别实验纳入曲率均匀对照目标函数，若候选无增益则假设不成立
downstream_requirements:
- 真实目标函数数据集
- 基线LUT代码
- 可微双线性插值实现
quality_warnings:
- 三次idea.research_delegate均返回research child validation_exhausted，未获得任何已读PDF方法页证据；KB查询无命中。related_literature为空，research_links仅含一条如实记录委派失败的证据缺口条目。本方案全部技术内容为对双线性插值数学性质的直接推理，非文献引用。
- 局部高曲率与非均匀采样为待验证假设，非已观察事实
- 未声称完成任何真实PIMC仿真或性能提升
human_summary: 把二维LUT的均匀16×16网格改为可训练的非均匀张量积网格（保持双线性插值），让节点密度自适应集中到高曲率区域，在256参数预算内可能降低插值误差；此改动是否有效取决于目标函数曲率是否空间非均匀，属待验证假设而非已测增益。
handoff:
  version: idea.handoff.v1
  target_agent: experiment
  scope: method_proposal
  next_step: 实现基线（均匀16×16双线性LUT）与候选（可训练非均匀15×15张量积网格双线性LUT），在曲率空间非均匀与均匀两类合成目标函数上按evaluation_protocol训练并比较测试MSE。
  changes:
  - target: /method_spec/candidate_grid
    operation: add
    spec_ref: /method_spec/candidate_grid
    preserve: []
  - target: /method_spec/candidate_interp
    operation: add
    spec_ref: /method_spec/candidate_interp
    preserve: []
  - target: /method_spec/coordinate_param
    operation: add
    spec_ref: /method_spec/coordinate_param
    preserve: []
  - target: /method_spec/objective
    operation: add
    spec_ref: /method_spec/objective
    preserve: []
  - target: /method_spec/optimizer
    operation: add
    spec_ref: /method_spec/optimizer
    preserve: []
  - target: /method_spec/initialization
    operation: add
    spec_ref: /method_spec/initialization
    preserve: []
  - target: /method_spec/randomness
    operation: add
    spec_ref: /method_spec/randomness
    preserve: []
  - target: /method_spec/split
    operation: add
    spec_ref: /method_spec/split
    preserve: []
  verification_requirements:
  - id: VR1
    question: 在曲率空间非均匀的目标函数上，候选测试MSE是否显著低于基线？
    comparison: 候选 vs 基线，同训练数据与目标，仅架构不同
    metric: 测试集均方误差
    decision_rule_ref: /decision_rule
  - id: VR2
    question: 在曲率近似均匀的目标函数上，候选是否不显著劣于基线？
    comparison: 候选 vs 基线，同训练数据与目标
    metric: 测试集均方误差
    decision_rule_ref: /decision_rule
  required_context:
  - kind: baseline_code
    description: 均匀16×16双线性LUT的可运行实现（含可微插值与训练循环）
    reason: 作为对照臂与候选的起点，缺失则无法执行受控比较
    blocks_execution: true
  - kind: data_description
    description: 曲率空间非均匀与均匀两类合成目标函数及其采样/划分定义
    reason: 判别实验需要可复现的数据生成与train/validation/test划分
    blocks_execution: true
  - kind: execution_environment
    description: 支持自动微分的数值环境（如PyTorch）
    reason: 坐标与值均需端到端梯度训练
    blocks_execution: true
method_spec:
  baseline_grid:
    description: 均匀16×16网格，节点坐标固定为x_i=-1+2i/15, y_j=-1+2j/15 (i,j=0..15)，节点值V_ij为256个可训练实参数
    node_count: 256
    trainable: 256个节点值
    fixed: 全部节点坐标固定均匀
  baseline_interp:
    description: 双线性插值：对(x,y)所在单元，局部坐标u=(x-x_i)/(x_{i+1}-x_i), w=(y-y_j)/(y_{j+1}-y_j)，输出f=(1-u)(1-w)V_ij+u(1-w)V_{i+1,j}+(1-u)wV_{i,j+1}+uwV_{i+1,j+1}
    formula: f=(1-u)(1-w)V_ij+u(1-w)V_{i+1,j}+(1-u)wV_{i,j+1}+uwV_{i+1,j+1}
  candidate_grid:
    description: 非均匀15×15张量积网格。x坐标{x_0..x_14}与y坐标{y_0..y_14}，端点固定x_0=y_0=-1, x_14=y_14=1。节点值W_ij共225个可训练实参数，位于(x_i,y_j)
    node_count: 225
    trainable_values: 225
    trainable_coords: 26
    total_trainable: 251
  coordinate_param:
    description: 单调性保证的坐标参数化。每轴14个间隙权重g_k=softplus(theta_k)>0 (k=1..14)，theta_k为可训练实参数。x_i=-1+2*(sum_{k<=i}g_k)/(sum_{k=1..14}g_k)，故x_0=-1,x_14=1且严格递增。两轴共28个theta，归一化后等效26个自由内部坐标参数。
    formula: x_i=-1+2*(sum_{k<=i}softplus(theta_k))/(sum_{k=1..14}softplus(theta_k))
    trainable: 28个theta(两轴各14)，归一化后等效26个自由内部坐标
    monotonic: true
  candidate_interp:
    description: 与基线相同的双线性插值公式，但单元由非均匀坐标确定：u=(x-x_i)/(x_{i+1}-x_i), w=(y-y_j)/(y_{j+1}-y_j)，输出f=(1-u)(1-w)W_ij+u(1-w)W_{i+1,j}+(1-u)wW_{i,j+1}+uwW_{i+1,j+1}
    formula: f=(1-u)(1-w)W_ij+u(1-w)W_{i+1,j}+(1-u)wW_{i,j+1}+uwW_{i+1,j+1}
  objective:
    description: 训练目标为训练集均方误差L=(1/|D_train|)sum_{(x,y,t)}(f(x,y)-t)^2，t为目标真值。基线仅对V求梯度；候选对W与theta均求梯度。
    formula: L=(1/|D_train|)*sum(f(x,y)-t)^2
  optimizer:
    description: Adam。基线：节点值V学习率lr_v。候选：节点值W学习率lr_v，坐标theta学习率lr_c=lr_v/10（坐标梯度通过单元定位传播，需更小步长保稳定）。
    lr_v: 0.001
    lr_c: 0.0001
  initialization:
    description: 节点值初始化为0。候选坐标theta初始化为使softplus(theta_k)全相等，即初始为均匀网格（softplus(theta)=2/14），保证候选从基线均匀网格出发。
    value_init: '0'
    coord_init: 均匀网格对应值
  randomness:
    description: 随机源为训练样本的蒙特卡洛采样与Adam的随机初始化。跨seed变化源：训练点采样位置与节点值/坐标的随机初始化。seed集合见evaluation_protocol.randomness。
    sources:
    - 训练点采样
    - 参数随机初始化
  split:
    description: 每类目标函数独立生成。训练集D_train为[-1,1]^2上N_train个均匀随机点；验证集D_val与测试集D_test各N_eval个独立均匀随机点，与训练集不相交（重新采样）。
    N_train: 20000
    N_eval: 5000
  target_functions:
    description: 两类合成目标：(A)曲率空间非均匀，如f=exp(-((x-0.5)^2+(y+0.5)^2)/0.01)的窄高斯峰叠加平滑背景；(B)曲率近似均匀，如f=sin(pi*x)*cos(pi*y)或低阶多项式。此为判别实验用合成目标，非真实数据。
    type_A: 局部窄高斯峰+平滑背景
    type_B: 平滑双线性/低阶多项式
decision_rule:
  metric: 测试集均方误差
  direction: 越低越好
  comparison: 候选 vs 基线，同训练数据与目标，仅架构不同（isolates_architecture=true）
  resampling_unit: 独立随机seed（每个seed重新采样训练/验证/测试点并重新初始化）
  accept: 在曲率非均匀目标A上，候选测试MSE的均值比基线低至少10%，且配对检验显著（p<0.05）
  reject: 在目标A上候选不显著低于基线，或在曲率均匀目标B上候选显著劣于基线
  inconclusive: 差异在噪声范围内，无法判定显著
  seeds:
  - 1
  - 2
  - 3
  - 4
  - 5
research_links:
- delegation_id: 527b962fd6104392b2d2d5406577c92a
  insight_id: no_verified_report
  method_spec_ref: /method_spec/candidate_grid
  adaptation_reason: 本次idea.research_delegate委派返回research child validation_exhausted，未返回任何已读PDF方法页证据或verified
    research_report。因此本条目如实记录证据缺口而非论文迁移：候选网格设计（可训练非均匀张量积网格）不来自任何已读文献，而是基于双线性插值单元内误差随单元尺寸平方与曲率乘积增长的数学推理。若后续获得真实文献证据，需据此重新评估该设计并补充related_literature。
evaluation_protocol:
  version: idea.evaluation.v1
  datasets:
  - id: A_train
    role: train
    spec_ref: /method_spec/split
  - id: A_val
    role: validation
    spec_ref: /method_spec/split
  - id: A_test
    role: test
    spec_ref: /method_spec/split
  - id: B_train
    role: train
    spec_ref: /method_spec/split
  - id: B_val
    role: validation
    spec_ref: /method_spec/split
  - id: B_test
    role: test
    spec_ref: /method_spec/split
  objectives:
    mse_A:
      spec_ref: /method_spec/objective
      data_refs:
      - A_train
    mse_B:
      spec_ref: /method_spec/objective
      data_refs:
      - B_train
  arms:
    baseline:
      training_data_refs:
      - A_train
      assessment_data_refs:
      - A_test
      objective_ref: /evaluation_protocol/objectives/mse_A
      optimizer_ref: /method_spec/optimizer
      initialization_ref: /method_spec/initialization
    candidate:
      training_data_refs:
      - A_train
      assessment_data_refs:
      - A_test
      objective_ref: /evaluation_protocol/objectives/mse_A
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
    - name: train_point_sampling
      spec_ref: /method_spec/randomness
    - name: param_init
      spec_ref: /method_spec/randomness
  comparison:
    isolates_architecture: true
    differences_justification: 两臂共享训练数据A_train、目标mse_A、优化器与初始化，唯一差异为网格结构（均匀16×16固定坐标
      vs 非均匀15×15可训练坐标），故隔离架构效应。目标B的对照在独立臂中运行以检验非均匀采样假设的适用边界。
parameter_budget:
  unit: real_scalar
  variables:
    n: 15
    n_values: 225
    n_coord_free: 26
    n_theta: 28
  baseline_formula: '256'
  baseline_parameters: 256
  baseline_components:
  - name: V
    formula: '256'
    dtype: real
    shape:
    - 16
    - 16
  candidate_formula: 225+26
  candidate_parameters: 251
  candidate_components:
  - name: W
    formula: '225'
    dtype: real
    shape:
    - 15
    - 15
  - name: theta_x
    formula: '14'
    dtype: real
    shape:
    - 14
  - name: theta_y
    formula: '14'
    dtype: real
    shape:
    - 14
  evaluation_cases:
  - name: curvature_nonuniform_A
    variables:
      n: 15
      n_values: 225
      n_coord_free: 26
      n_theta: 28
    baseline_parameters: 256
    candidate_parameters: 251
  - name: curvature_uniform_B
    variables:
      n: 15
      n_values: 225
      n_coord_free: 26
      n_theta: 28
    baseline_parameters: 256
    candidate_parameters: 251
signal_contract:
  input_domain: '[-1,1]^2'
  output: real scalar
  interpolation: bilinear
  trainable_baseline: 256 node values
  trainable_candidate: 225 node values + 26 free interior coordinates (28 theta, 2
    fixed by normalization)
  fixed: endpoints at -1 and 1 on each axis
  monotonicity: guaranteed by cumulative softplus gap parameterization
alternatives:
- name: alt_nonuniform_16x16_shared_coords
  feasible: false
  parameters: 284
  components:
  - name: V
    formula: '256'
    dtype: real
    shape:
    - 16
    - 16
  - name: coords
    formula: '28'
    dtype: real
    shape:
    - 28
  reason: 16×16值(256)+28自由坐标=284>256，超出预算，不可行
- name: alt_rect_nonuniform_16x15
  feasible: true
  parameters: 254
  components:
  - name: W
    formula: '240'
    dtype: real
    shape:
    - 16
    - 15
  - name: theta_x
    formula: '14'
    dtype: real
    shape:
    - 14
  reason: 仅x轴坐标可训练（14自由内部坐标），y轴保持均匀，值16×15=240，总254≤256可行。作为对照检验二维坐标自适应是否必要，还是仅一维自适应即可。因值参数少于主候选且只自适应一维，作为备选而非主候选。
- name: alt_nonuniform_15x15_candidate
  feasible: true
  parameters: 251
  components:
  - name: W
    formula: '225'
    dtype: real
    shape:
    - 15
    - 15
  - name: theta_x
    formula: '14'
    dtype: real
    shape:
    - 14
  - name: theta_y
    formula: '14'
    dtype: real
    shape:
    - 14
  reason: 251≤256可行，直接检验二维非均匀采样假设，作为主候选
ablation_plan:
- name: abl_fixed_coords
  description: 候选坐标theta冻结为均匀网格值（不更新），仅训练225个节点值W。检验坐标可训练性本身是否带来增益。此消融下theta不参与梯度更新，W的225个参数在Adam下各自对损失有梯度（双线性权重非零），行为改变为退化为均匀15×15网格。
  changes: theta不训练
  decision_rule_ref: /decision_rule
- name: abl_no_coord_grad
  description: 候选坐标theta仍可训练但梯度被截断（stop-gradient），仅W更新。检验坐标梯度传播路径是否有效。
  changes: theta梯度截断
  decision_rule_ref: /decision_rule
- name: abl_uniform_15x15
  description: 候选坐标固定均匀且仅225个值可训练，与基线256值对比，检验减少31个值参数本身的影响（隔离网格尺寸效应）。
  changes: 15×15均匀网格
  decision_rule_ref: /decision_rule
- name: abl_curvature_uniform_target
  description: 在曲率均匀目标B上运行候选vs基线，检验非均匀采样增益是否依赖曲率空间非均匀。
  changes: 目标函数类型
  decision_rule_ref: /decision_rule
---

把二维LUT的均匀16×16网格改为可训练的非均匀张量积网格（保持双线性插值），让节点密度自适应集中到高曲率区域，在256参数预算内可能降低插值误差；此改动是否有效取决于目标函数曲率是否空间非均匀，属待验证假设而非已测增益。