---
schema: diagnosis.v1
project: moe-pimc
agent: diagnosis
failed_node: execution
root_cause: "Describe the root cause of the failure (>= 8 chars)."
recommended_action: revise_coding   # retry | revise_coding | revise_experiment | manual
target_node: coding
attempt: 1
confidence: 0.6
evidence:
  - "Paste the key error line(s) or log excerpt here."
---

# Diagnosis

## What failed
Which node failed and the observable symptom.

## Root cause
The most likely cause, grounded in the error and upstream artifacts.

## Recommended action
Why re-routing to `target_node` (or retrying) should fix it.
