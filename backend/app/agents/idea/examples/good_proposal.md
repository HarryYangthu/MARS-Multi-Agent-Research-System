# 合格 Proposal 示例 — PIMC

下面是一个能让 Experiment Agent 直接展开消融、并在双载波 PIM simulator 上跑出可比 RES
曲线的合格 proposal 样例。

---
schema: proposal.v1
title: "深化 canceller memory taps 以在 beam 切换下达到 RES 噪声地板"
hypothesis: >
  在真实 PIM memory ≈12 taps 的双载波场景下,当前 8-expert canceller 的有效 memory 深度
  不足,残留 memory effect 使 RES 停在约 -21 dB(batch mean)。把 expert_count 提升到
  16(等效更深 memory taps)并保持 order=7,可使 RES 降到 ≤ -26 dB(gate),loss ≤ 0.04。
metrics:
  primary: RES        # dB, 越低越好, gate <= -26 (mean)
  secondary: [loss, pim_suppression_db, ape_deg]
ablations:
  - expert_count: [8, 12, 16, 24]   # -> canceller memory taps
  - order: [5, 7]                    # 奇数阶
  - router_type: [soft, hard-top2]
  - snr_db: [30]
baseline_safety: additive_only       # 不改 Paper_Total_0327 / forward(x, stream_label)
---

## 研究问题
beam/layer 切换瞬态下,memory-polynomial canceller 的有效 memory 深度是否是 RES 触底的瓶颈?

## 可证伪假设
expert_count 8→16 使 RES 由约 -21 dB 降到 ≤ -26 dB(mean);若 RES 不随 taps 增加而下降,假设证伪。

## 理论依据
PIM 由奇数阶 Volterra memory polynomial 生成,真实 memory ≈12 taps;canceller taps 少于真实
memory 时无法对齐延迟分量,残留功率抬高 RES。3 阶互调落在 2f1-f2 / 2f2-f1,是主要被抵消能量。

## 可测试预测
RES(mean)单调下降至噪声地板附近(约 -29 dB);PIM suppression(=-RES)相应升高;APE 下降。

## 最小实验建议
先扫 expert_count ∈ {8,12,16,24} × router_type ∈ {soft, hard-top2},snr_db=30,固定 order=7。

## 风险与证据缺口
hard-top2 可能在切换边界引入路由抖动;改动须 ADDITIVE,不得触碰 baseline 保护面(Gate 5)。

---

## 合格的关键特征
- 假设直接映射到 simulator 旋钮(expert_count→memory taps、order、router_type、snr_db)。
- 指标方向写对:RES 越低越好,gate ≤ -26 dB(mean);loss ≤ 0.04(max);PIM suppression 越高越好。
- 给出最小消融,且预留了"加深 canceller"的扫描空间,便于 self-heal 回到 Experiment。
- 显式声明 ADDITIVE,尊重 `Paper_Total_0327` 与 `forward(x, stream_label)` 冻结面。
- 引用信号模型(12-tap memory、奇数阶互调)作为理论依据,而非凭空设指标。
