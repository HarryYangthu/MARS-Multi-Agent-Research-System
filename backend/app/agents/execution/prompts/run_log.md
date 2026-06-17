# Run Log Prompt

生成 `run_log.v1` 时，你正在汇总 **dual-carrier PIM cancellation** 的一批仿真结果：
memory-polynomial canceller（可选 MoE router）在 fs=184.32 MHz、双载波 f1=30 / f2=38 MHz
的合成信号上拟合，逐 ablation 跑出 RES / loss 曲线。无 GPU 时走 CPU 实跑
（`execution/pim_cancellation.py`，~30720 复点，亚秒级）；无数据时走 mock。

输出重点：

- `run_id` + 对应的 `experiment_plan` / `code_spec` chain_refs。
- `planned_experiments`：逐条 ablation，记下实际消费的旋钮——
  `expert_count`（→ canceller memory taps，真实 PIM memory ≈ 12 taps）、
  `order ∈ {1,3,5,7,9}`（odd）、`router_type ∈ {soft, hard-topk/hard-top2}`、
  `snr_db`、`learning_rate`。
- `status`：每条 `completed / failed / skipped`，失败给 traceback 头部。
- `metrics`：**RES（dB，越低越好，gate 为 batch mean RES ≤ -26 dB）**、
  `loss`（max ≤ 0.04）、PIM suppression dB（= -RES，越高越好）、APE（残余相位误差，度）。
  逐 ablation 列值 + batch 聚合值。**禁止把 RES 描述成"越高越好"。**
- `plots`：每条 ablation 的 loss/RES 收敛曲线路径；`logs`：逐 step 流式日志路径。
- `failure_summary`：若 batch mean RES 未过 gate，指明**最可能是 ablation 欠配
  （memory taps 太浅，无法抵消 ~12 taps 的真实 PIM memory）**，为 Commander 的
  self-heal 提供"加深 canceller / 提高 expert_count"的可执行线索。
- 配置指纹 + seed，保证可复现、可追溯。
