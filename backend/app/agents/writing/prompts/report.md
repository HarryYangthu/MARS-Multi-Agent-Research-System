# Report Prompt

生成 `report.v1` 时，读取 proposal、experiment plan、code spec、run log 和 HITL 记录，
把这条 **dual-carrier PIM cancellation**（memory-polynomial canceller + router）研究链路
写成可审阅、可复现的报告。

输出重点：

- **摘要**：本轮试图把 PIM 残余压到什么水平（用 RES dB，**越低越好**），是否过 gate
  （batch mean RES ≤ -26 dB，loss ≤ 0.04）。一句话给出最优 ablation 与对应 RES。
- **方法**：memory-polynomial canceller 拟合奇数阶 Volterra（order ∈ {1,3,5,7,9}）；
  router（soft / hard-topk）如何按 stream 路由；canceller memory taps 与真实 PIM
  memory（≈ 12 taps）的关系。
- **实验设置**：fs=184.32 MHz、f1=30 / f2=38 MHz、~30720 复点、snr_db、learning_rate；
  ablation 矩阵（`expert_count` → memory taps、`order`、`router_type`）与 seed。
- **结果和失败分析**：逐 ablation 列 RES（dB）/ PIM suppression（dB，= -RES）/ APE（度）/ loss；
  指出哪些过 gate、哪些因 memory taps 太浅留下残余 memory effects。**禁止把 RES 写成越高越好，
  禁止夸大抵消量。**
- **局限性**：合成信号 vs 真实 PIM、CPU 实跑 vs 全 7 层训练、未覆盖的 order/router 组合。
- **下一步**：若本轮触发 self-heal，说明回退到哪个 Agent（通常 Experiment 加深 sweep）及其依据。
- 全文严格引用 chain_refs；区分已验证结果 / 失败结果 / 仍未验证假设；面向研究合作者与导师。

