# 不合格 Proposal 示例 — MoE-PIMC

下面是一个会被打回的 PIM 抵消 proposal 样例,以及它为什么不合格。

---
schema: proposal.v1
title: "换一个更强的 router 让 PIM 抵消更好"
hypothesis: "用一个全新的 attention router 替换现有 routing,把 RES 拉高,效果会更好。"
---

## 想法
我觉得现在的 router 太简单了,可以试试更先进的 router,顺便重写一下 baseline 的
`forward(x, stream_label)` 多传几个参数,再在真实射频功放上测一下 PIM 功耗。

---

## 为什么不合格(对照检查)
- **指标约定写反**:说"把 RES 拉高",但 RES 越低越好(gate ≤ -26 dB, mean),"拉高"= 变差。
- **没有可证伪假设 / 无数量级**:只说"更好",没给 RES / loss 的目标方向与量级。
- **没有可实验旋钮**:没落到 simulator 真正消费的 `expert_count`(→memory taps)、
  `order∈{1,3,5,7,9}`、`router_type∈{soft, hard-top2}`、`snr_db`、`learning_rate`。
- **违反 baseline 保护**:要改 `forward(x, stream_label)` 签名、动 baseline 方法体——Gate 5 直接拦;
  改动应当 ADDITIVE(新模块/子类)。
- **依赖现有代码库无法复现的条件**:"真实射频功放测 PIM 功耗",而 V0 只有双载波 simulator +
  `data_gen.py` 合成数据,无硬件 proxy。
- **把猜测当结论**:"更先进的 router 一定更好",无 KB / 历史 run 证据支撑。
