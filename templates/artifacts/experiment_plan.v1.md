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

# Experiment plan

Body describes hypothesis testing strategy.
