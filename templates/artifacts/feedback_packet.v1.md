---
schema: feedback_packet.v1
project: pimc
agent: commander
run_id: example_run
target_agent: coding
attempt: 2
source_attempt: 1
confidence: 0.75
why_this_agent: Code change risk is the most likely cause of the metric miss.
evidence_refs:
  - execution/metrics.json
failed_metrics:
  - metric: loss
    observed: 0.12
    target: 0.04
    direction: lte
do_next:
  - Produce a focused revision that addresses the failed metric.
avoid_repeating:
  - Do not change protected baseline paths.
context_refs:
  - diagnosis/diagnosis.v1.md
memory_candidates: []
---

# Commander Feedback Packet

This packet is the bounded context handoff from the Commander Agent to the
target Agent for the next feedback-loop attempt.
