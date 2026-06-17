# Idea Proposal Prompt — MoE-PIMC

基于 `research_summary.v1.md` 和 `evidence_index.v1.json` 生成 PIM 抵消方向的
proposal。proposal 必须服务于一件事:让下游 Experiment Agent 能直接把它变成一组
ablation,在双载波 PIM simulator 上跑出可比的 RES / loss 曲线。

proposal 至少要表达:

- **研究问题**:针对 FDD Massive MIMO + beam/layer 切换下的 PIM 抵消,通常落在
  "在切换瞬态/跨 stream 复用时如何用 MoE router 分配 canceller 容量"。
- **可证伪假设**:必须能映射到 simulator 旋钮与指标。例:
  "在 12-tap 真实 memory 下,将 `expert_count` 从 8 提到 16(等效更深 memory taps),
  RES 从约 -21 dB(mean)降到 ≤ -26 dB(gate),loss ≤ 0.04"。
- **新颖性**:相对 `Paper_Total_0327` baseline 与历史 run 的差异点。
- **理论依据**:对照信号模型——奇数阶 memory polynomial、3 阶互调落在 2f1-f2 / 2f2-f1、
  memory taps 不足时残留 memory effect → RES 抬高。
- **可测试预测**:给出指标方向与数量级,用正确约定(RES 越低越好、PIM suppression 越高越好)。
- **最小实验建议**:列出要扫的旋钮,只用 simulator 真正消费的:`expert_count`(→memory taps)、
  `order∈{1,3,5,7,9}`、`router_type∈{soft, hard-top2}`、`snr_db`、`learning_rate`。
- **风险和证据缺口**:特别指出会不会触碰 baseline 保护面。
- **下游 Experiment Agent 需要关注的变量和指标**:主指标 RES(gate ≤ -26 dB, mean),
  辅指标 loss(≤ 0.04, max)、PIM suppression dB、APE(度)。

硬约束:
- 任何编码改动必须 **ADDITIVE**(新模块/子类),不得改 `Paper_Total_0327` 方法体、
  `forward(x, stream_label)` 签名,不得写 `baseline/` 或 `production_interface/`(Gate 5 会拦)。
- 不提出现有 simulator 无法度量或复现的方向。
- 若 RES gate 多半因 ablation 网格过浅(memory 太短)而 miss,这是 Experiment 设计问题,
  proposal 应预留更深 canceller 的扫描空间,便于 self-heal(default_target=experiment)。
