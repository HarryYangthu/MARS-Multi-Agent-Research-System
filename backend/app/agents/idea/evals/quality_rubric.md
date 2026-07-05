# Idea 输出质量评估 Rubric — PIMC

评估一个 PIM 抵消 proposal 是否合格。每个维度给 0/1/2 分,任一维度为 0 直接判不合格。

## 评分维度

- **Testability(可证伪性)**:假设能否映射到双载波 PIM simulator
  (`backend/app/execution/pim_cancellation.py`)的旋钮并跑出 RES 曲线?
  - 0:只说"更好/更强",无旋钮无量级。
  - 2:明确扫 `expert_count`(→memory taps)/`order∈{1,3,5,7,9}`/`router_type∈{soft,hard-top2}`/
    `snr_db`/`learning_rate`,并给出 RES 的目标方向与量级。

- **Metric Correctness(指标约定)**:
  - 0:把 **RES 写成"越高越好"**,或混淆 RES 与 PIM suppression。
  - 2:RES 越低越好(gate ≤ -26 dB, mean)、loss ≤ 0.04(max)、PIM suppression=-RES 越高越好、
    APE 越低越好;数量级合理(噪声地板 ≈ -29 dB,差 ≈ -20 dB)。

- **Evidence**:是否引用本地 KB / 历史 run / 信号模型(12-tap memory、奇数阶互调
  落在 2f1-f2 / 2f2-f1)作为依据,而非凭空设指标。

- **Downstream Readiness**:Experiment Agent 能否一键把它展开成多条可比 RES 曲线的 ablation?

- **Baseline Safety**:是否 ADDITIVE?有无触碰 `Paper_Total_0327` 方法体、
  `forward(x, stream_label)` 签名、`baseline/`、`production_interface/`(任一触碰 → Gate 5 拦 → 0 分)。

- **Novelty**:相对 baseline 与历史 run 是否有实质差异(新模块/新容量配置/新 routing 策略)。

## self-heal 友好度(加分项)
RES gate miss 多半是 ablation 网格过浅(memory 太短)。proposal 若预留"加深 canceller"
的扫描空间,便于 Commander 在 `default_target=experiment`(`max_iterations=2`)下一次性补足容量,记加分。
