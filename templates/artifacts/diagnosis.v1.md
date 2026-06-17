---
schema: diagnosis.v1
project: moe-pimc
agent: bridge
run_id: example_run
attempt: 1
passed: false
failed_metrics:
  - metric: loss
    observed: 0.12
    target: 0.02
    direction: lte
    gap: 0.1
    aggregation: max
suspected_causes:
  - kind: metrics_gap
    summary: Loss exceeded the configured threshold.
    severity: high
    evidence:
      - execution/metrics.json
recommended_target: coding
recommended_action: Generate a focused code patch for human review.
evidence_refs:
  - execution/metrics.json
budget_status: within_budget
---

# Diagnosis

Replace this template with a bridge-generated diagnosis.
