---
schema: experiment_plan.v1
project: moe-pimc
agent: experiment
upstream_artifact: idea_proposal.approved.md
variables:
  independent: ["expert_count", "router_type"]
  controlled: ["batch_size", "epochs"]
  dependent: ["RES", "PIM", "APE"]
metrics:
  primary: "RES"
  secondary: ["PIM", "APE", "param_count"]
baseline_ref:
  matched_run_id: null
  match_score: null
  reuse_decision: rerun
ablations:
  - name: "expert_count_4"
    config: { expert_count: 4 }
  - name: "expert_count_16"
    config: { expert_count: 16 }
estimated_runs: 8
estimated_gpu_hours: 24
---

# 实验计划

本计划用于描述假设验证策略、实验变量、消融组合、基线复用决策与资源估算。正文必须使用中文说明实验目的、变量控制方式、指标判定标准、风险与预期结果；技术标识如 schema、metric、文件路径和模型名保持原样。
