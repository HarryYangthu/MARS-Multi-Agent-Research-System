---
schema: experiment_plan.v1
project: "PROJECT_ID_FROM_TASK"
agent: experiment
variables:
  independent: ["PARAMETER_FROM_APPROVED_HYPOTHESIS"]
  controlled: []
  dependent: ["METRIC_FROM_PROJECT_CONTRACT"]
metrics:
  primary: "METRIC_FROM_PROJECT_CONTRACT"
  secondary: []
baseline_ref:
  matched_run_id: null
  match_score: null
  reuse_decision: null
ablations:
  - name: "REPLACE_WITH_APPROVED_EXPERIMENT"
    config: {}
estimated_runs: 1
---

# 实验计划格式参考

以上名称、空配置和数量仅说明字段结构，不是任务参数或可执行计划。实际输出必须替换为当前项目、已批准假设与真实执行入口支持的配置，核对实验数量与预算。缺少必要信息时明确阻断，不沿用占位值。

正文用中文说明假设、对照、数据划分、自变量/控制变量、指标单位与方向、目标与容差、汇总方式、保护范围及预算依据。未明确的资源估算不填入数字；技术标识保持原样。计划不证明实验已执行或达到目标。
