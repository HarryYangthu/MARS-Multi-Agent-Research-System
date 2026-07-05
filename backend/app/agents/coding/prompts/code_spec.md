# Code Spec Prompt (pimc)

你在为 **PIMC** canceller 出代码规格:把 experiment plan 落成对
**memory-polynomial canceller + router**(消双载波 odd-order PIM)的可审核改动。
先读 experiment plan 的 ablation grid 与指标门限、再读 `projects/pimc/AGENTS.md`
的 baseline 保护规则,然后产出 `code_spec.v1`(YAML frontmatter + markdown body)。

## 输出重点

- **files_changed**:逐文件列出。改动必须 **ADDITIVE**——新增 module / 子类 / keyword-only 参数,
  绝不触碰冻结面。
- **implementation_plan**:把 ablation 旋钮接到 canceller。仿真器真正消费的是
  `expert_count`(→ memory taps,真实 PIM memory ≈ 12)、`order` ∈ {1,3,5,7,9}(奇)、
  `router_type` ∈ {soft, hard-topk/hard-top2}、`snr_db`、`learning_rate`;不要发明没人读的参数。
- **tests**:验证指标方向正确——`RES`(dB)**越低越好**,断言能命中
  `RES <= -26 dB`(mean) 与 `loss <= 0.04`(max);加深 memory taps 时 RES 应单调下降。
- **baseline_compat**:显式声明未改 `Paper_Total_0327`、未改 `forward(x, stream_label)`、
  未写 `baseline/**` 与 `production_interface/**`(否则 Gate 5 在 dispatch 路径直接 block)。
- **patch diff**:最小、可回滚、tensor op 前后带 shape 注释。
- **rollback_notes**:如何一键回退到改动前。

## 冻结面(改动即被 Gate 5 拦截)

- `libs/Model.py:Paper_Total_0327` — 方法体 / 构造签名冻结。
- `forward(x, stream_label)` — 第三个位置参数必须是 `stream_label`;新参数只能用带默认值的
  keyword-only,**禁止**位置重排。
- `baseline/**`、`production_interface/**` — 对 MARS 只读。

正确做法:新建 `DeepMemoryCanceller(Paper_Total_0327)` 之类的子类或独立 module,
在子类里扩 memory taps / router,baseline 原样保留作对照。

## 与自愈环的关系

`allowed_targets=[experiment, coding]`,`default_target=experiment`。多数 RES 未过门是
experiment 侧 ablation 欠配,不该来 coding;只有当确属代码缺陷(如 canceller 没正确接上
`expert_count`、basis 构造错、shape 不匹配)时,Commander 才会回退到 Coding,
此时 patch 仍须 ADDITIVE 且不破冻结面。
