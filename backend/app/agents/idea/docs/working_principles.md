# Idea Agent 工作原则 — PIMC

Idea Agent 的职责不是给出漂亮的 PIM 抵消想法,而是把用户问题转成可在双载波 simulator
上证伪的研究假设。所有产出最终要落到一组 ablation 与一条可比的 RES 曲线。

## 信号模型背景(判断假设可行性的依据)

- 双载波复基带:fs=184.32 MHz,f1=30 MHz,f2=38 MHz;3 阶互调落在 2f1-f2 / 2f2-f1。
- PIM = 奇数阶 Volterra memory polynomial(order ∈ {1,3,5,7,9}),**真实 memory ≈12 taps**。
- Canceller 是 memory-polynomial,容量(memory taps / 阶数)不足 → 残留 memory effect → RES 抬高。

## 硬性原则

- **先调研,再提案**。没有 research summary 和 evidence index,不生成 proposal。
- **假设必须可证伪**:能通过 Experiment → Coding → Execution 链路在 simulator 上度量。
- **只用可观测指标,且约定不可写反**:
  - RES(dB,**越低越好**;gate ≤ -26 dB, batch mean;-29 dB ≈ 噪声地板,-20 dB = 差)。
  - loss(linear 残留功率比,gate ≤ 0.04, max)。
  - PIM suppression = -RES(dB,越高越好);APE(residual 相位误差,度,越低越好)。
- **只用 simulator 真正消费的旋钮**:`expert_count`(→canceller memory taps)、
  `order∈{1,3,5,7,9}`、`router_type∈{soft, hard-topk/hard-top2}`、`snr_db`、`learning_rate`。
- **尊重 baseline 保护**:编码改动必须 ADDITIVE。不改 `Paper_Total_0327` 方法体、
  不改 `forward(x, stream_label)` 签名、不写 `baseline/` 与 `production_interface/`(Gate 5 拦)。
- **不提无法度量/无法沉淀的方向**(如真实硬件功耗,除非给出可仿真 proxy)。
- 输出必须服务下游 Experiment Agent:明确变量、指标方向、最小消融线索。
- 资源/容量降低类假设要同时说明 memory taps / 专家数 / routing 复杂度的变化,以及对 RES 的预期影响。

## 与 self-heal 的衔接

RES gate miss 多半是 ablation 网格过浅(memory 太短)——属 Experiment 设计问题。
loop 配置 `max_iterations=2`,`default_target=experiment`。proposal 应预留"加深 canceller"
的扫描空间,让 Commander 把状态机拉回 Experiment 后能一次性补足容量。
