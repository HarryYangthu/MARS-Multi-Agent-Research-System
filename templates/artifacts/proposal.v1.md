---
schema: proposal.v1
project: moe-pimc
agent: idea
created: 2026-05-04T10:32:00Z
research_question: "How can ATK-MoE further reduce compute under 8L config while preserving RES performance?"
hypothesis: "A simplified hard top-2 router degrades RES by less than 1.5 dB while cutting MAC count by ~30%."
novelty: "Combines hard top-2 routing with stream-aware gating; not present in surveyed literature."
theoretical_basis: "Sparse expert activation reduces effective compute; PIM cancellation preserves dominant expert path."
constraints:
  - "baseline_compat: required"
  - "ASIC_resource: ≤40% reduction"
related_literature:
  - title: "MoE Routing Survey 2024"
    url: "https://arxiv.org/abs/2404.00000"
debate_summary:
  rounds: 2
  consensus: "Hard top-2 router is preferred over learned gates for ASIC simplicity."
---

# Idea proposal

Body of the proposal goes here.
