---
schema: proposal.v1
project: pimc
agent: idea
created: '2026-09-07T19:30:00Z'
research_question: 如何在不显著增加参数（≤1.2 倍，本次评测假设）的前提下，改善 PIMC 非线性层中 2D LUT 的非线性表达能力？
hypothesis: 将 2D LUT 的固定均匀网格改为每轴可学习的自由网格（控制点数量不变，仅新增少量单调性保证的节点位置参数），使相同数量的控制点可集中到非线性变化剧烈的区域，从而在参数增幅约
  15% 内提升表达能力；二阶差分平滑正则可抑制节点聚集引起的振荡。
novelty: 把 FR-KAN 的自由网格思想（可学习、单调性保证的节点）迁移到 2D 双线性 LUT 上，作为对 spline-LUT 固定均匀网格的最小改动；这是方法迁移推断，非论文原结论，需实验验证。
theoretical_basis: FR-KAN 表明自由网格可增加唯一节点数从而提升表达能力；spline-LUT 论文给出控制点可训练、插值平滑的 LUT 框架。二者结合：控制点仍为可训练
  LUT 条目，节点位置额外可训练。
constraints:
- 参数量上限为基线 1.2 倍（本次评测假设，非业务事实）
- 方法仅符号化定义，未接入真实基线/数据/GPU
- 不伪装已读生产代码
- 物理信号与硬件架构不得凭字母猜测
related_literature:
- title: Gradient-Adaptive Spline-Interpolated LUT Methods for Low-Complexity Digital
    Predistortion
  url: http://arxiv.org/abs/1907.02350v4
- title: 'Free-Knots Kolmogorov-Arnold Network: On the Analysis of Spline Knots and
    Advancing Stability'
  url: http://arxiv.org/abs/2501.09283v1
testable_predictions:
- prediction: 在含局部陡峭区域的参考非线性函数上，自由网格候选在相同控制点数（K=12）下取得低于固定均匀网格基线的 NMSE_held。
  metric: NMSE_held
  expected_direction: 候选 ≤ 0.9×基线
  success_threshold: 5 个种子中 ≥4 个胜出
- prediction: 自由网格相对固定网格的增益在空间曲率非均匀的函数上大于在平滑全局函数上。
  metric: NMSE_held 差
  expected_direction: 非均匀曲率函数增益更大
  success_threshold: 增益差 > 0
- prediction: 二阶差分平滑正则降低节点聚集时的跨种子方差（稳定性），代价是轻微 NMSE_held 上升。
  metric: 跨种子 NMSE_held 方差
  expected_direction: 加正则方差更低
  success_threshold: 方差下降且 NMSE_held 上升 <5%
- prediction: 参数量比 candidate_parameters/baseline_parameters ≤ 1.2。
  metric: 参数比
  expected_direction: 166/144≈1.153
  success_threshold: ≤1.2
experiment_hint:
  variables:
  - K=12（每轴节点数，固定）
  - lambda=1e-3（固定常数，见 training_objective 选择协议）
  - delta=1/(2*(K-1))（最小节点间隙，固定）
  - sigma_init=0.05（C 初始化扰动幅度，固定）
  - 自由网格开关（消融）
  - 参考函数曲率分布（f1 平滑全局 vs f2 局部陡峭）
  metrics:
  - NMSE_held
  - 跨种子方差
  - 参数比
  minimal_ablations:
  - A1 自由网格关+正则开
  - A2 自由网格开+正则关
  - A3 仅单轴自由网格
  - A4 K=12 与 K=16 分辨率对比
evidence_refs:
- ref: 1907.02350v4 eq(3)-(11)
  kind: pdf_page
  summary: 定义 span index in、归一化横坐标 un、B-spline 基矩阵 B3、控制点向量 c 与 injection 输出；控制点可训练。
- ref: 2501.09283v1 eq(5)-(8)
  kind: pdf_page
  summary: FR-KAN 激活含可学习自由网格与 C2 二阶导数平滑正则，用于提升表达力并稳定训练。
risk_register:
- risk: 节点可能聚集/塌缩导致病态单元或除零
  severity: medium
  mitigation: softmax+最小间隙下限 δ 参数化，保证任意训练状态下最终坐标最小间隙 ≥δ>0，末单元分母恒 ≥δ
- risk: 参考函数曲率均匀时自由网格无增益
  severity: medium
  mitigation: 消融 A1 与预测 2 检验；无增益则回退固定网格
- risk: 双线性插值仅 C0 连续，平滑性受限
  severity: low
  mitigation: 备选方向 AltB（双三次/Catmull-Rom）作为对照
- risk: 两个 LUT 轴(u,v)的物理含义未确认
  severity: high
  mitigation: required_context 中 blocks_execution=true，需项目确认
- risk: C 初始化扰动幅度 sigma_init 若过大可能使基线/候选起点偏离公平
  severity: low
  mitigation: sigma_init 固定为 0.05 且对基线/候选共用同一扰动协议，保证 equal-budget 公平起点
human_summary: 将 PIMC 非线性层的 2D LUT 从固定均匀网格改为每轴可学习的自由网格，控制点数量不变、仅新增少量单调性保证的节点位置参数，使相同控制点可集中到非线性剧烈的区域以提升表达能力。另加入可选的二阶差分平滑正则抑制节点聚集导致的振荡；参数增幅约
  15%，未做实验验证。
handoff:
  version: idea.handoff.v1
  target_agent: experiment
  scope: method_proposal
  next_step: 在符号化参考模型上实现固定均匀网格基线（K=12 双线性 LUT）与自由网格候选，两者共用同一目标 L=NMSE_train+λR 与同一
    C 初始化扰动协议（确定性线性映射 + N(0,sigma_init^2) 扰动，sigma_init=0.05），用固定采样协议生成的 D_train 优化，在未触碰的
    D_held 上按 decision_rule 比较 NMSE_held 并执行 4 项消融。
  changes:
  - target: /method_spec/candidate
    operation: add
    spec_ref: /method_spec/candidate
    preserve:
    - /method_spec/baseline
  - target: /method_spec/training_objective
    operation: add
    spec_ref: /method_spec/training_objective
    preserve: []
  - target: /method_spec/baseline
    operation: modify
    spec_ref: /method_spec/baseline
    preserve:
    - /method_spec/baseline/name
    - /method_spec/baseline/knots_per_axis
    - /method_spec/baseline/output
  verification_requirements:
  - id: VR1
    question: 自由网格候选是否在 NMSE_held 上优于固定均匀网格基线？
    comparison: 候选 NMSE_held vs 基线 NMSE_held（相同未触碰留出采样 D_held，相同 C 初始化扰动协议）
    metric: NMSE_held
    decision_rule_ref: /decision_rule
  - id: VR2
    question: 参数量比是否 ≤1.2？
    comparison: candidate_parameters/baseline_parameters vs 1.2
    metric: 参数比
    decision_rule_ref: /decision_rule
  required_context:
  - kind: baseline_code
    description: 项目真实 2D LUT 非线性层的实现代码
    reason: 本方法仅符号化定义，需真实基线代码才能落地
    blocks_execution: true
  - kind: data_description
    description: PIMC 非线性层实际输入分布与参考非线性
    reason: 需真实输入分布与目标非线性才能评估 NMSE
    blocks_execution: true
  - kind: background
    description: 两个 LUT 轴(u,v)的物理含义及输出为实/复
    reason: 不得凭字母猜测物理信号；决定输入归一化与相位契约
    blocks_execution: true
method_spec:
  task_scope: method_proposal：为 PIMC 非线性层提出改善 2D LUT 非线性表达能力的可实现方案，仅符号化定义，不声称项目就绪或已测性能。
  symbolic_reference_model: 定义两个符号化参考非线性：f1(u,v)=0.5*sin(2*pi*u)*cos(2*pi*v)（平滑全局，曲率均匀）；f2(u,v)=0.5*sin(2*pi*u)*cos(2*pi*v)+0.8*exp(-((u-0.7)^2+(v-0.3)^2)/0.01)（含局部陡峭凸起，曲率非均匀）。定义域
    (u,v)∈[0,1]^2。f2 用于检验自由网格的节点集中能力，f1 作为曲率均匀对照。此为项目真实非线性层的占位，非项目数据。
  input_output_contract: 输入：两个归一化实坐标 u,v∈[0,1]（符号化；其物理含义需项目确认）。输出：一个实标量 y。LUT 为无记忆映射
    (u,v)→y。若两轴源自复信号（如幅度-相位），映射定义在归一化幅度-相位平面，相位契约需项目确认。
  baseline:
    name: fixed_uniform_2d_bilinear_lut
    knots_per_axis: 12
    fixed_knots: x_k = k/(K-1), k=0..K-1（两轴相同，固定）
    cell_index: i = min(floor(u*(K-1)), K-2); j = min(floor(v*(K-1)), K-2)
    local_coords: alpha = u*(K-1) - i; beta = v*(K-1) - j
    output: y = (1-alpha)*(1-beta)*C[i,j] + alpha*(1-beta)*C[i+1,j] + (1-alpha)*beta*C[i,j+1]
      + alpha*beta*C[i+1,j+1]
    trainable: C ∈ R^{K×K}（K^2=144 个控制点）
    fixed: 均匀网格节点（固定）
    initialization: C 初始化为在均匀节点处复现参考线性映射 y=0.5*(u+v) 的值：C[i,j]=0.5*(i/(K-1)+j/(K-1))，对所有
      i,j∈{0..K-1}。随后对每个训练种子 s 施加独立随机扰动：C_s[i,j] = C[i,j] + eps_s[i,j]，其中 eps_s[i,j]
      ~ N(0, sigma_init^2)，sigma_init=0.05 固定。此扰动是种子间差异的唯一来源（见 training_objective
      的随机性来源说明）。
    training_objective: 与候选共用同一目标 L(θ)=NMSE_train(θ)+λ*R(C)，λ=1e-3 固定（见 training_objective）。基线不使用无正则目标；A1
      消融（固定网格+正则开）即等价于基线本身，用于隔离正则贡献。
    parameter_count: 144
  candidate:
    name: free_grid_2d_bilinear_lut
    knots_per_axis: 12
    min_gap_delta: δ = 1/(2*(K-1))（固定超参，K=12 时 δ=1/22≈0.0455）
    logits: a^u_k ∈ R, k=1..K-1（可训练）；a^v_k 同理
    softmax_weights: s^u_k = exp(a^u_k)/Σ_{m=1..K-1} exp(a^u_m)，满足 Σ s^u_k = 1
    gaps: g^u_k = δ + (1-(K-1)*δ)*s^u_k，满足 g^u_k ≥ δ 且 Σ g^u_k = 1
    knots: t^u_0 = 0; t^u_k = Σ_{m=1..k} g^u_m, k=1..K-1；则 0=t^u_0 < t^u_1 < ... <
      t^u_{K-1}=1，任意有限 logits 下严格单调且最小间隙=δ
    cell_index: i = max index with t^u_i <= u（searchsorted，clamp 到 [0,K-2]）; j 同理
    local_coords: alpha = (u - t^u_i)/(t^u_{i+1}-t^u_i); beta = (v - t^v_j)/(t^v_{j+1}-t^v_j)；分母
      ≥ δ > 0 恒成立
    output: y = (1-alpha)*(1-beta)*C[i,j] + alpha*(1-beta)*C[i+1,j] + (1-alpha)*beta*C[i,j+1]
      + alpha*beta*C[i+1,j+1]（与基线同形，仅网格不同）
    trainable: C ∈ R^{K×K}（144）+ a^u ∈ R^{K-1} + a^v ∈ R^{K-1}（共 22 个节点 logits）
    fixed: δ（最小间隙）、K、λ、网格端点 0 与 1
    parameter_count: 166
    parameter_ratio: 166/144 ≈ 1.153 ≤ 1.2
    feasible_delta_range: 'δ ∈ (0, 1/(K-1)]。K=12: δ≤1/11≈0.0909；K=16: δ≤1/15≈0.0667。默认
      δ=1/(2(K-1)) 保证最小间隙有意义且允许约 2 倍密度集中。'
    initialization: a^u_k = a^v_k = 0 → s_k = 1/(K-1) → g_k = δ + (1-(K-1)δ)/(K-1)
      = 1/(K-1)，初始网格均匀，候选从基线行为出发（公平起点）。C 初始化与基线完全一致：先设 C[i,j]=0.5*(i/(K-1)+j/(K-1))（复现线性映射
      y=0.5*(u+v) 在均匀节点上的值），再对每个训练种子 s 施加同一扰动协议 C_s[i,j] = C[i,j] + eps_s[i,j]，eps_s[i,j]
      ~ N(0, sigma_init^2)，sigma_init=0.05。关键：对同一种子 s，基线 C_s 与候选 C_s 使用相同的 eps_s 实现（共享随机数流），使种子间差异与两模型间差异可分离。
    training_objective: 与基线共用同一目标 L(θ)=NMSE_train(θ)+λ*R(C)，λ=1e-3 固定。
    parameter_count_note: 166 个可训练标量（144 控制点 + 22 节点 logits）
  trainable_and_fixed: 可训练：控制点 C（K^2 个）与两轴节点 logits a^u,a^v（各 K-1 个）。固定：δ、K、λ、网格端点
    0 与 1、sigma_init。
  boundary_handling: 输入 u,v 先 clamp 到 [0,1]；自由网格节点由构造保证在 [0,1] 内且严格单调（最小间隙 δ）；单元索引
    clamp 到 [0,K-2]，避免越界；末单元分母 t_{i+1}-t_i ≥ δ > 0，u=1 处无除零。
  training_objective: 数据采样协议（固定，可复现）：对每个参考函数 f∈{f1,f2}，独立生成 D_train 与 D_held。D_train：N_train=4096
    个 (u,v) 点，u,v 各自独立均匀采样于 [0,1]（随机采样，非网格），用固定随机种子 seed_train 生成一次，5 个训练种子共用同一 D_train。D_held：N_held=4096
    个 (u,v) 点，独立均匀随机采样，用固定随机种子 seed_held 生成一次，训练/选择全程不触碰，仅用于最终决策。两数据集不相交（不同种子）。随机性来源（种子间差异的唯一来源）：对每个训练种子
    s∈{1..5}，C 初始化扰动 eps_s ~ N(0, sigma_init^2) 独立采样（sigma_init=0.05 固定）；基线 C_s 与候选
    C_s 对同一 s 共享同一 eps_s。优化器 Adam（确定性，无 mini-batch 随机性、无权重噪声），学习率 1e-3，固定 2000 步，全量
    D_train 参与。因此同一模型在固定 s 下训练结果确定，跨种子差异完全来自 eps_s。优化目标 L(θ) = NMSE_train(θ) + λ*R(C)，其中
    θ={C,a^u,a^v}（基线 θ={C}），NMSE_train = Σ_{(u,v)∈D_train}(y-f)^2 / Σ_{(u,v)∈D_train}
    f^2，仅在 D_train 上计算。λ 为固定常数 λ=1e-3，对所有模型、种子、函数统一，不做逐函数调参（避免选择偏差）；λ 的敏感性由消融 A2（λ=0）检验。最终决策只用
    NMSE_held = Σ_{(u,v)∈D_held}(y-f)^2 / Σ_{(u,v)∈D_held} f^2。
  regularizer: R = Σ_{i=1..K-2, j=1..K-2} (C[i-1,j]+C[i+1,j]+C[i,j-1]+C[i,j+1]-4*C[i,j])^2，即控制点场上的离散拉普拉斯（二阶差分）惩罚，λ=1e-3
    为固定权重。此为对 FR-KAN 二阶导数平滑思想在双线性 LUT 上的迁移推断，非论文原结论。
  limitations: 双线性插值仅 C0 连续；自由网格增益依赖参考函数曲率分布；物理轴含义未确认；参数比 1.2 为评测假设；未做实验验证。
  paper_findings_vs_inference: 论文原结论：spline-LUT 控制点可训练且插值平滑；FR-KAN 自由网格增加唯一节点数、C2
    正则稳定训练。本文推断：把自由网格迁移到 2D 双线性 LUT 可提升表达力。待验证假设：该迁移在 PIMC 非线性层上有效。
decision_rule:
  metric: NMSE_held（在未触碰留出集 D_held 上，越低越好）
  direction: lower_is_better
  comparison: 候选 NMSE_held vs 基线 NMSE_held，在相同 D_held 上，跨 5 个独立训练种子取均值；种子间差异来自 C 初始化扰动
    eps_s（sigma_init=0.05），基线/候选对同一 s 共享同一 eps_s
  resampling_unit: 5 个独立训练种子（每个种子用相同固定 D_held 评估）
  accept: 候选平均 NMSE_held ≤ 0.9 × 基线平均 NMSE_held，且候选在 5 个种子中 ≥4 个胜出
  reject: 基线平均 NMSE_held ≤ 候选平均 NMSE_held（无改善），且基线在 5 个种子中 ≥4 个胜出
  inconclusive: 其余情况（改善不足或种子间不一致）
parameter_budget:
  unit: real_scalar
  variables:
    K: 12
  baseline_formula: K*K
  baseline_parameters: 144
  baseline_components:
  - name: control_points_C
    formula: K*K
    dtype: real
    shape:
    - K
    - K
  candidate_formula: K*K + 2*(K-1)
  candidate_parameters: 166
  candidate_components:
  - name: control_points_C
    formula: K*K
    dtype: real
    shape:
    - K
    - K
  - name: knot_logits_a_u
    formula: K-1
    dtype: real
    shape:
    - K-1
  - name: knot_logits_a_v
    formula: K-1
    dtype: real
    shape:
    - K-1
signal_contract:
  input: u,v ∈ [0,1] 归一化实坐标
  output: y 实标量
  phase_semantics: 无记忆映射 (u,v)→y；若轴源自复信号需项目确认相位契约
  boundary: 输入 clamp 到 [0,1]，索引 clamp 到 [0,K-2]，末单元分母 ≥δ>0
alternatives:
- name: AltA_free_grid_learnable_knots
  feasible: true
  parameters: 166
  components:
  - name: control_points_C
    formula: K*K
    dtype: real
    shape:
    - K
    - K
  - name: knot_logits_a_u
    formula: K-1
    dtype: real
    shape:
    - K-1
  - name: knot_logits_a_v
    formula: K-1
    dtype: real
    shape:
    - K-1
  selection_reason: 选中：最小改动，控制点不变仅加节点位置，可集中到陡峭区域，参数比 1.153≤1.2，且 softmax+δ 参数化保证任意训练状态严格单调无除零
- name: AltB_fixed_grid_bicubic
  feasible: true
  parameters: 144
  components:
  - name: control_points_C
    formula: K*K
    dtype: real
    shape:
    - K
    - K
  selection_reason: 拒绝：双三次/Catmull-Rom 提升平滑度(C1)但不增加自由度或分辨率，无法集中节点到陡峭区；需边界处理且可能过冲
- name: AltC_fixed_quantile_knots
  feasible: true
  parameters: 144
  components:
  - name: control_points_C
    formula: K*K
    dtype: real
    shape:
    - K
    - K
  selection_reason: 拒绝：适应输入密度而非函数曲率，无梯度自适应，需校准且对分布漂移脆弱
ablation_plan:
- name: A1_free_knots_off_reg_on
  change: 固定均匀网格 + 平滑正则开（λ=1e-3）。此即基线本身（基线已定义使用 L=NMSE_train+λR）。
  purpose: 隔离正则单独是否有效，检验增益来自节点还是平滑；在 D_held 上改变 NMSE_held 行为
- name: A2_free_knots_on_reg_off
  change: 自由网格 + 平滑正则关（λ=0）
  purpose: 检验节点聚集时正则是否为稳定训练所必需；在 D_held 上改变 NMSE_held 行为
- name: A3_single_axis_free
  change: 仅 u 轴自由、v 轴固定均匀
  purpose: 检验是否两轴都需自由节点，能否进一步降参；在 D_held 上改变 NMSE_held 行为
- name: A4_resolution_K
  change: K=12（基线 144/候选 166，比 1.153）与 K=16（基线 256/候选 286，比 1.117）对比，两者均在 1.2 预算内
  purpose: 检验分辨率敏感度与参数比-性能权衡；在 D_held 上改变 NMSE_held 行为
---

将 PIMC 非线性层的 2D LUT 从固定均匀网格改为每轴可学习的自由网格，控制点数量不变、仅新增少量单调性保证的节点位置参数，使相同控制点可集中到非线性剧烈的区域以提升表达能力。另加入可选的二阶差分平滑正则抑制节点聚集导致的振荡；参数增幅约 15%，未做实验验证。