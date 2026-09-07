---
schema: proposal.v1
project: pimc
agent: idea
created: '2026-09-07T19:30:00Z'
research_question: 如何在不使参数量大幅膨胀（≤1.2×基线）的前提下，改善 PIMC 非线性层中 2D LUT 的非线性表达能力？
hypothesis: 将 2D LUT 的固定均匀网格替换为累积差量参数化的可学习自由网格，并在每个网格单元内用双线性插值（而非最近邻/零阶保持）读取，可在几乎不增加参数量（仅增加每轴
  G-1 个间距增量）的情况下提升对光滑二维非线性函数的逼近精度。
novelty: 把 Free-Knot KAN 的自由网格思想（2501.09283）与 spline-interpolated LUT 的可微插值结构（1907.02350）迁移到二维
  LUT 非线性层：用累积差量参数化（softplus 间距）实现严格单调且处处可微的自由网格，配合双线性插值，使控制点数量不变而节位可自适应移动。
theoretical_basis: Free-Knot KAN 论证固定网格共享节位限制表达力、可学习节位可提高节数上限（Theorem 5.1）；spline-interpolated
  LUT 论证可微插值读取使控制点可经梯度学习（1907.02350 复梯度学习规则）。二者结合到二维网格，控制点张量形状不变，仅增加少量间距参数。
constraints:
- 参数量上限为基线 1.2 倍（本次评测假设，非业务事实）
- PIMC 缩写、物理信号与硬件架构未定义，需在 required_context 中确认
- 本测试无真实基线/数据/GPU，仅定义符号化参考模型
- 方法迁移须区分论文原结论、本文推断与待验证假设
related_literature:
- title: Gradient-Adaptive Spline-Interpolated LUT Methods for Low-Complexity Digital
    Predistortion
  url: http://arxiv.org/abs/1907.02350v4
- title: 'Free-Knots Kolmogorov-Arnold Network: On the Analysis of Spline Knots and
    Advancing Stability'
  url: http://arxiv.org/abs/2501.09283v1
testable_predictions:
- prediction: 在定义的光滑二维目标函数集上，候选（自由网格+双线性插值）的验证集 RMSE 低于基线（固定网格+最近邻）
  metric: validation RMSE
  expected_direction: lower
  success_threshold: 相对降低 ≥5%
- prediction: 候选参数量不超过基线 1.2 倍
  metric: candidate_parameters/baseline_parameters
  expected_direction: ≤1.2
  success_threshold: ratio ≤ 1.2
- prediction: 训练后自由网格间距显著偏离均匀间距（证明节位确实移动而非退化为固定网格）
  metric: mean|d_k - Δ|/Δ
  expected_direction: '>0.05'
  success_threshold: mean|d_k-Δ|/Δ ≥ 0.05
experiment_hint:
  variables:
  - grid_size_G
  - input_domain
  metrics:
  - validation RMSE
  - parameter_ratio
  - mean_spacing_deviation
  minimal_ablations:
  - 固定网格+双线性插值（隔离插值贡献）
  - 自由网格+最近邻（隔离自由网格贡献）
  - 无平滑正则 λ=0（隔离正则贡献）
evidence_refs:
- ref: 1907.02350v4
  kind: pdf
  summary: 注入式 spline-interpolated LUT：输出=输入+输入×g^T c，控制点全零时退化为恒等映射；Q=K+PSP；复梯度学习规则（已读第3-5页）
- ref: 2501.09283v1
  kind: pdf
  summary: Free-Knot KAN：G*=Sort(G+b_g) 自由网格提高节数上限（Theorem 5.1）；C2 二阶导数正则稳定训练；神经元分组降参（已读第4-7页）
risk_register:
- risk: 自由网格间距参数可能使节位过度聚集或稀疏，导致局部过拟合或欠拟合
  severity: medium
  mitigation: 累积差量参数化保证严格单调；平滑正则约束控制点场；间距偏离度作为监控指标
- risk: PIMC 缩写与真实硬件/信号语义未定义，方案可能不匹配真实层结构
  severity: high
  mitigation: required_context 中列为 blocks_execution 前置，需确认后再落地
- risk: 双线性插值在网格边界外需外推，可能引入不稳定
  severity: medium
  mitigation: 输入归一化到 [a,b]，边界外钳制到端点控制点
- risk: 自由网格间距参数化增加训练自由度，可能使优化更困难
  severity: low
  mitigation: 间距初始化为均匀值（确定性起点），softplus 保证正间距，训练稳定
human_summary: 将 PIMC 非线性层的 2D LUT 从固定均匀网格+最近邻读取改为可学习自由网格（累积差量参数化）+双线性插值读取，使控制点数量不变而节位可自适应移动，从而在不显著增加参数的前提下提升非线性表达能力。
handoff:
  version: idea.handoff.v1
  target_agent: experiment
  scope: method_proposal
  next_step: 按 /method_spec 实现符号化基线（固定均匀网格+最近邻 2D LUT）与候选（累积差量自由网格+双线性插值 2D LUT），在
    /method_spec/synthetic_family 定义的光滑二维函数集上按 /evaluation_protocol 跑三种子、三组消融，报告验证
    RMSE、参数量比值与间距偏离度。
  changes:
  - target: 2D LUT 非线性层读取方式
    operation: replace
    spec_ref: /method_spec/candidate_forward
    preserve:
    - 输入输出维度
    - 控制点张量形状 G×G
  - target: 网格定义
    operation: add
    spec_ref: /method_spec/free_grid
    preserve: []
  - target: 训练目标正则项
    operation: modify
    spec_ref: /method_spec/training_objective
    preserve:
    - MSE 主项
  verification_requirements:
  - id: VR1
    question: 候选是否在光滑二维函数集上验证 RMSE 低于基线？
    comparison: 候选 vs 基线，同训练数据同目标同优化器
    metric: validation RMSE
    decision_rule_ref: /decision_rule
  - id: VR2
    question: 候选参数量是否 ≤1.2× 基线？
    comparison: candidate_parameters vs 1.2*baseline_parameters
    metric: parameter_ratio
    decision_rule_ref: /decision_rule
  - id: VR3
    question: 自由网格间距是否显著偏离均匀间距（节位确实移动）？
    comparison: mean|d_k-Δ|/Δ vs 0.05
    metric: mean_spacing_deviation
    decision_rule_ref: /decision_rule
  required_context:
  - kind: background
    description: PIMC 缩写的确切含义、其非线性层输入/输出物理信号语义、2D LUT 的两个输入轴分别代表什么物理量
    reason: 缩写与信号语义未定义，不能凭字母猜测；方案需匹配真实层结构
    blocks_execution: true
  - kind: baseline_code
    description: 现有 2D LUT 非线性层的参考实现（网格构造、读取、训练管线）
    reason: 需在真实基线上做架构隔离对比，本测试无真实代码
    blocks_execution: true
  - kind: data_description
    description: 用于训练/验证/测试的真实输入分布与目标函数定义（若存在）
    reason: 评估协议需真实数据，本测试仅定义符号化合成数据
    blocks_execution: true
  - kind: metric_definition
    description: 验证 RMSE 与参数量计数的权威定义
    reason: 需与项目既有指标口径一致
    blocks_execution: false
method_spec:
  baseline_forward: 基线 2D LUT 直接映射：输入 (u,v) 归一化到 [a,b]^2=[-1,1]^2。固定均匀网格 G={g_0,...,g_{G-1}}，g_j=a+j·Δ，Δ=(b-a)/(G-1)。最近邻（零阶保持）读取：找最近网格索引
    (i,j)=argmin 距离，输出 y=C[i,j]。控制点张量 C∈R^{G×G} 为唯一可训练量。
  candidate_forward: 候选 2D LUT 直接映射：输入 (u,v) 归一化到 [a,b]^2。自由网格 Gx*={gx*_0,...,gx*_{G-1}}、Gy*={gy*_0,...,gy*_{G-1}}
    由 /method_spec/free_grid 生成。双线性读取：先对 u 做端点钳制 u_c=clamp(u, gx*_0, gx*_{G-1})，再找相邻索引
    i=floor((u_c-gx*_0)/d_avg) 作为初始猜测，随后线性扫描/二分查找确定满足 gx*_i≤u_c≤gx*_{i+1} 的唯一 i∈{0,...,G-2}（因网格严格单调，该
    i 存在且唯一；当 u_c=gx*_{G-1} 时取 i=G-2，即最右单元）。对 v 同理得 j∈{0,...,G-2}。归一化系数 sx=(u_c-gx*_i)/(gx*_{i+1}-gx*_i)，sy=(v_c-gy*_j)/(gy*_{j+1}-gy*_j)。输出
    y=(1-sx)(1-sy)C[i,j]+sx(1-sy)C[i+1,j]+(1-sx)sy·C[i,j+1]+sx·sy·C[i+1,j+1]。端点处理：u_c=gx*_{G-1}
    时 sx=1 且取 i=G-2，此时 C[i+1,j]=C[G-1,j] 为端点控制点，输出退化为端点列与相邻列的插值，定义良好。
  free_grid: 累积差量参数化自由网格（可微、严格单调、无需 Sort）：对每个轴，可学习参数 δ∈R^{G-1}。间距 d_k=softplus(δ_k)>0，k=0..G-2。累积
    h_0=0，h_j=Σ_{k=0}^{j-1}d_k，j=1..G-1。总长 L=h_{G-1}。网格点 g*_j=a+(b-a)·h_j/L。性质：g*_0=a，g*_{G-1}=b，中间点严格单调递增；softplus
    与归一化均处处可微，梯度可回传。初始化：δ_k=log(exp(Δ)-1) 使 d_k=Δ（均匀间距），即初始网格退化为均匀网格（确定性、可复现）。
  bilinear_read: 双线性读取定义见 /method_spec/candidate_forward。每单元仅用 4 个相邻控制点，计算量 O(1) 与网格大小无关。
  trainable: 可训练量：控制点张量 C∈R^{G×G}（G^2 个）；每轴间距增量 δ∈R^{G-1}（两轴共 2(G-1) 个）。固定量：网格范围 [a,b]=[-1,1]、网格数
    G、插值阶数（双线性）。
  initialization: C 初始化为零加独立高斯抖动 C~N(0,σ^2)，σ=0.01，抖动随种子变化。δ 确定性初始化：δ_k=log(exp(Δ)-1)
    使 d_k=Δ（均匀间距），不随种子变化。
  boundary: 输入归一化到 [a,b]=[-1,1]。u<gx*_0 或 u>gx*_{G-1} 时先钳制 u_c=clamp(u,gx*_0,gx*_{G-1})
    再读取；v 同理。自由网格端点固定为 a 与 b，故边界钳制等价于取端点控制点。域内闭区间端点 u_c=gx*_{G-1} 由 /method_spec/candidate_forward
    的 i=G-2 规则处理。
  training_objective: L=(1/N)Σ_n(y_true_n-y_pred_n)^2 + λ·R_smooth。主项为均方误差。R_smooth
    为控制点场离散二阶差分平滑正则：R_smooth=Σ_{i=1}^{G-2}Σ_{j=0}^{G-1}(C[i+1,j]-2C[i,j]+C[i-1,j])^2
    + Σ_{i=0}^{G-1}Σ_{j=1}^{G-2}(C[i,j+1]-2C[i,j]+C[i,j-1])^2。该正则惩罚相邻控制点之间的二阶差分（跨单元曲率），约束控制点场平滑性，与双线性插值兼容（控制点场平滑则插值输出平滑）。注意：这不是对输出函数
    C^2 连续性的约束（双线性插值单元内二阶偏导恒为 0），而是对离散控制点场的平滑约束。λ 默认 1e-3，作为超参数在验证集上选择。
  optimizer: Adam，学习率 1e-3，batch 256，epochs 200。
  randomness: 种子 {2024,7,42}。随机源：(1) 数据采样器——合成函数输入在 [-1,1]^2 上均匀采样，随种子变化；(2) C 初始化抖动
    C~N(0,0.01^2)，随种子变化。δ 初始化确定性（均匀间距），不随种子变化。
  split_train: 合成函数集按 70% 划分训练子集，输入在 [-1,1]^2 上均匀随机采样，随种子变化。
  split_val: 合成函数集按 15% 划分验证子集，输入在 [-1,1]^2 上均匀随机采样，与训练子集不相交，随种子变化。
  split_test: 合成函数集按 15% 划分测试子集，输入在 [-1,1]^2 上均匀随机采样，与训练/验证子集不相交，随种子变化。
  synthetic_family: 定义在 [-1,1]^2 上的光滑二维目标函数族，每个函数独立采样 N=20000 点（训练 14000/验证 3000/测试
    3000）：f1(u,v)=exp(-(u^2+v^2)/0.5)（高斯峰）；f2(u,v)=sin(2πu)cos(2πv)（周期光滑）；f3(u,v)=0.5u^3+0.3v^2+0.2uv（多项式）；f4(u,v)=1/(1+25(u^2+v^2))（Runge
    型陡峭峰，考验非均匀节位）。每个函数独立训练与评估，报告各函数验证 RMSE 的均值。
  input_output_contract: 输入 (u,v)∈[-1,1]^2 实值；输出为实值标量 y=LUT(u,v)。本方法为幅度-相位无关的二维到一维标量映射。若
    PIMC 语义需要复数输出，则 C 取复值（参数量×2），需在 required_context 中确认。
  parameter_accounting: 基线参数 = G^2（仅控制点 C）。候选参数 = G^2 + 2(G-1)（控制点 C + 两轴间距增量 δ）。
  smoothness_regularization: 见 /method_spec/training_objective 中 R_smooth 定义。λ 默认
    1e-3，在验证集上选择。
  grid_monotonicity: 累积差量参数化保证 g*_j 严格单调递增（softplus 输出恒正），无需 Sort，梯度可回传。
decision_rule:
  metric: validation RMSE（越低越好）
  direction: lower_is_better
  comparison: 候选 vs 基线，同训练数据同目标同优化器，三种子取均值
  resampling_unit: 独立种子（数据采样器与 C 初始化抖动随种子变化）
  accept: 候选均值 RMSE 相对基线降低 ≥5% 且参数量比值 ≤1.2，且三种子中至少 2 种子候选更优（即至少 2 种子候选 RMSE < 基线 RMSE）
  reject: 候选均值 RMSE 相对基线降低 <0%（即候选均值不低于基线）或参数量比值 >1.2
  inconclusive: 其余情形：候选均值 RMSE 相对降低在 [0%,5%) 之间，或均值降低 ≥5% 但三种子中少于 2 种子候选更优（种子间方向不一致）。判定优先级：先检查
    reject 条件（参数量比值 >1.2 或均值不降则直接 reject），再检查 accept 条件（均值降≥5% 且参比≤1.2 且≥2 种子更优），其余归入
    inconclusive。三条件互斥且穷尽。
  priority_order: reject 优先于 accept 优先于 inconclusive；先判参数量比值 >1.2 或均值相对变化 <0% 则 reject；否则若均值降≥5%
    且≥2 种子更优则 accept；否则 inconclusive。
  exhaustive: 对任意三种子结果，必落入且仅落入 accept/reject/inconclusive 之一：参比>1.2 或均值不降→reject；否则均值降≥5%
    且≥2 种子更优→accept；否则（均值降在[0,5%) 或均值降≥5% 但<2 种子更优）→inconclusive。
evaluation_protocol:
  version: idea.evaluation.v1
  datasets:
  - id: synth_train
    role: train
    spec_ref: /method_spec/split_train
  - id: synth_val
    role: validation
    spec_ref: /method_spec/split_val
  - id: synth_test
    role: test
    spec_ref: /method_spec/split_test
  objectives:
    fit_mse:
      spec_ref: /method_spec/training_objective
      data_refs:
      - synth_train
  arms:
    baseline:
      training_data_refs:
      - synth_train
      assessment_data_refs:
      - synth_val
      - synth_test
      objective_ref: /evaluation_protocol/objectives/fit_mse
      optimizer_ref: /method_spec/optimizer
      initialization_ref: /method_spec/initialization
    candidate:
      training_data_refs:
      - synth_train
      assessment_data_refs:
      - synth_val
      - synth_test
      objective_ref: /evaluation_protocol/objectives/fit_mse
      optimizer_ref: /method_spec/optimizer
      initialization_ref: /method_spec/initialization
  randomness:
    seeds:
    - 2024
    - 7
    - 42
    sources:
    - name: data_sampler
      spec_ref: /method_spec/randomness
    - name: init_jitter
      spec_ref: /method_spec/randomness
  comparison:
    isolates_architecture: true
    differences_justification: 两臂共享训练数据、目标、优化器与初始化起点（C 零+抖动、δ 均匀间距），唯一差异为网格是否可学习（固定均匀
      vs 累积差量自由）与读取方式（最近邻 vs 双线性），故隔离的是架构本身。
parameter_budget:
  unit: real_scalar
  variables:
    G: 16
  baseline_formula: G*G
  baseline_parameters: 256
  baseline_components:
  - name: control_points_C
    formula: G*G
    dtype: real
    shape:
    - G
    - G
  candidate_formula: G*G + 2*(G-1)
  candidate_parameters: 286
  candidate_components:
  - name: control_points_C
    formula: G*G
    dtype: real
    shape:
    - G
    - G
  - name: spacing_delta_x
    formula: G-1
    dtype: real
    shape:
    - G-1
  - name: spacing_delta_y
    formula: G-1
    dtype: real
    shape:
    - G-1
  evaluation_cases:
  - name: G10
    variables:
      G: 10
    baseline_parameters: 100
    candidate_parameters: 118
  - name: G16
    variables:
      G: 16
    baseline_parameters: 256
    candidate_parameters: 286
  - name: G32
    variables:
      G: 32
    baseline_parameters: 1024
    candidate_parameters: 1086
signal_contract:
  input_domain: '[-1,1]^2 实值'
  output: 实值标量（若 PIMC 需复数则 C 复值，参数量×2，需确认）
  phase_semantics: 本方法为幅度-相位无关的二维到一维标量映射；复数扩展需确认 PIMC 语义
  grid_monotonicity: 累积差量参数化保证 g*_j 严格单调递增，无需 Sort，梯度可回传
alternatives:
- name: AltA_固定网格+双线性插值
  feasible: true
  parameters: 256
  components:
  - name: control_points_C
    formula: G*G
    dtype: real
    shape:
    - G
    - G
  selection_reason: 仅改读取方式（最近邻→双线性），隔离插值贡献；作为消融臂 A1。若单独双线性已达标则自由网格非必需。
- name: AltB_自由网格+最近邻
  feasible: true
  parameters: 286
  components:
  - name: control_points_C
    formula: G*G
    dtype: real
    shape:
    - G
    - G
  - name: spacing_delta_x
    formula: G-1
    dtype: real
    shape:
    - G-1
  - name: spacing_delta_y
    formula: G-1
    dtype: real
    shape:
    - G-1
  selection_reason: 仅改网格（固定→自由），隔离自由网格贡献；作为消融臂 A2。若单独自由网格已达标则双线性非必需。
- name: AltC_自由网格+三次样条插值
  feasible: true
  parameters: 286
  components:
  - name: control_points_C
    formula: G*G
    dtype: real
    shape:
    - G
    - G
  - name: spacing_delta_x
    formula: G-1
    dtype: real
    shape:
    - G-1
  - name: spacing_delta_y
    formula: G-1
    dtype: real
    shape:
    - G-1
  selection_reason: 更高阶插值可能更光滑但计算量增（需更多相邻控制点）；作为备选，若双线性不足则升级，但需额外消融验证其增益是否超过计算成本。
ablation_plan:
- name: A1_固定网格+双线性插值
  operation: 去掉自由网格间距参数（δ 固定为均匀），保留双线性读取
  isolates: 双线性插值相对最近邻的贡献
  expected_behavior_change: 在光滑函数 f1-f4 上 RMSE 应低于基线（最近邻），因双线性消除零阶保持的阶梯伪影
- name: A2_自由网格+最近邻
  operation: 去掉双线性读取（回到最近邻），保留自由网格间距参数
  isolates: 自由网格相对固定网格的贡献
  expected_behavior_change: 节位移动应改善对非均匀曲率函数（f4 Runge 型）的逼近，因节位可聚集到陡峭区域
- name: A3_无平滑正则
  operation: 去掉 R_smooth 项（λ=0），保留自由网格+双线性
  isolates: 平滑正则对控制点场稳定性的贡献
  expected_behavior_change: 无正则时控制点场可能过拟合尖峰，验证 RMSE 方差增大
---

将 PIMC 非线性层的 2D LUT 从固定均匀网格+最近邻读取改为可学习自由网格（累积差量参数化）+双线性插值读取，使控制点数量不变而节位可自适应移动，从而在不显著增加参数的前提下提升非线性表达能力。