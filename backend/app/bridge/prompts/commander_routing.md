# Commander Routing Prompt

Commander 编排的是一条 **dual-carrier PIM cancellation**（memory-polynomial canceller + MoE router）
研究流水线：Idea → Experiment → Coding → Execution → Writing。根据用户输入与当前 run 状态判断：

- 是否从 Idea 开始（用户给的是一个 PIM/MoE 假设，还是直接的实验/代码请求）。
- 是否已有合规 `proposal`（Schema 校验过）可跳过 Idea，直接进 Experiment。
- 哪个子 Agent 是下一步阻塞点（前一产物缺失 / 未过 HITL / 未过系统 Gate）。
- 是否需要进入诊断或 self-heal 循环。**触发条件**：Execution 汇总后 batch mean RES > -26 dB
  或 loss > 0.04（RES 越低越好，未压到 gate）。

self-heal 路由（见 `projects/moe-pimc/diagnostics.yaml`）：

- `max_iterations = 2`，`allowed_targets = [experiment, coding]`，`default_target = experiment`。
- RES 未过 gate **默认归因为 ablation 欠配**——canceller memory taps 太浅，抵不住真实 PIM 的
  ≈ 12 taps memory——所以把状态机拉回 **Experiment** 加深 sweep（提高 `expert_count` → memory taps，
  必要时调 `order`）。仅当诊断指向实现缺陷（如 router_type/基函数构造错）才回退 **Coding**。
- 回退前必须先给出诊断依据（哪条 ablation、RES 差多少、归因哪个旋钮），不盲目重跑。

