# Agent I/O Schema reference

> Agent-facing and system schemas live under
> `backend/app/harness/schema/schemas/` and are validated by
> `harness/schema/validator.py`. JSON Schema draft 2020-12.

## Common rules

- Frontmatter is YAML, fenced with `---`.
- The `schema` field is the schema id (`proposal.v1` etc.) and is required.
- Datetime values may be unquoted ISO 8601 — the validator coerces them to strings before applying the schema.
- `additionalProperties: true` — frontmatter may carry extra fields without invalidation; downstream readers ignore unknowns.

---

## 1. `proposal.v1` (Idea Agent)

**Required**: `schema, project, agent, research_question, hypothesis, novelty`

**Examples** of optional fields: `created`, `theoretical_basis`, `constraints[]`, `related_literature[].title|.url`, `debate_summary.rounds|.consensus`.

```yaml
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
  consensus: "Hard top-2 router preferred over learned gates for ASIC simplicity."
---

# Idea proposal
…
```

---

## 2. `experiment_plan.v1` (Experiment Agent)

**Required**: `schema, project, agent, variables, metrics, ablations, estimated_runs`

`variables.independent` and `variables.dependent` must be non-empty arrays. `metrics.primary` must be a non-empty string. `ablations` must be ≥ 1 entry, each `{name, config: {...}}`.

```yaml
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
  - name: "expert_count_8"
    config: { expert_count: 8 }
  - name: "expert_count_16"
    config: { expert_count: 16 }
estimated_runs: 6
estimated_gpu_hours: 18
---
```

`baseline_ref.reuse_decision ∈ {rerun, reuse, modify, null}`.

---

## 3. `code_spec.v1` (Coding Agent)

**Required**: `schema, project, agent, target_lang, baseline_compat, files_changed`

`target_lang ∈ {python, c, cpp, rust}`. Each `files_changed` entry: `{path, type ∈ {modified, added, deleted, renamed}, risk ∈ {low, medium, high}}`. `baseline_compat.preserved: bool` is required; provide a non-empty `rationale` when preserved is False.

```yaml
---
schema: code_spec.v1
project: moe-pimc
agent: coding
upstream_artifact: experiment_plan.approved.md
target_lang: python
baseline_compat:
  preserved: true
  rationale: "forward(x, stream_label) signature unchanged; new Paper_Router_v2 added alongside existing Paper_Total_0327."
files_changed:
  - path: "libs/Model.py"
    type: modified
    risk: medium
  - path: "tests/test_router_v2.py"
    type: added
    risk: low
new_dependencies: []
test_coverage:
  unit_tests_added: 3
  baseline_smoke_test: pass
---
```

`test_coverage.baseline_smoke_test ∈ {pass, fail, skipped}`.

---

## 4. `run_log.v1` (Execution Agent — one per ablation)

**Required**: `schema, project, agent, run_id, status, metrics, fingerprint_hash`

`status ∈ {completed, failed, interrupted}`. `metrics` has at least one entry. `fingerprint_hash` is a hex SHA, optionally prefixed with `sha256:`.

```yaml
---
schema: run_log.v1
project: moe-pimc
agent: execution
upstream_artifact: code_spec.approved.md
run_id: "2026-05-04T2310_pimc_moe_ablation_run3"
batch_size: 512
gpu_used: ["L40S:1", "L40S:2"]
duration_seconds: 3420
status: completed
metrics:
  RES: -42.3
  PIM: -18.7
  APE: 23.6
fingerprint_hash: "sha256:abcd1234deadbeef0000aaaa"
is_mock: false
---
```

When `is_mock: true` the artifact came from `execution/mock_simulation.py`. Schema-shape is identical to a real run; downstream consumers can't distinguish without consulting `is_mock`.

---

## 5. `report.v1` (Writing Agent)

**Required**: `schema, project, agent, deliverable_type, target_audience, chain_refs`

`deliverable_type ∈ {research_report, paper_fragment, ppt_outline, tech_summary}`. `chain_refs` should reference the upstream artifacts the report integrates (relative paths inside `runs/<id>/`).

```yaml
---
schema: report.v1
project: moe-pimc
agent: writing
deliverable_type: research_report
target_audience: phd_advisor
chain_refs:
  proposal: idea_proposal.approved.md
  plan: experiment_plan.approved.md
  code: code_spec.approved.md
  runs:
    - execution/run_log_run1.md
    - execution/run_log_run2.md
debate_summary:
  rounds: 1
  reviewer_critiques:
    - "Discuss ASIC area implications more concretely."
    - "Add ablation against soft router baseline."
---
```

---

## 6. `diagnosis.v1` (Bridge outcome diagnosis)

**Required**: `schema, project, agent, run_id, attempt, passed, failed_metrics, suspected_causes, recommended_target, recommended_action, evidence_refs, budget_status`

`recommended_target ∈ {coding, experiment, idea, writing, none}`.
`budget_status ∈ {within_budget, exhausted, not_applicable}`.

```yaml
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
    target: 0.04
    direction: lte
    gap: 0.08
    aggregation: max
suspected_causes:
  - kind: metrics_gap
    summary: Loss exceeded the configured threshold.
    severity: high
    evidence: ["execution/metrics.json"]
recommended_target: coding
recommended_action: Generate a focused code patch for human review.
evidence_refs: ["execution/metrics.json"]
budget_status: within_budget
---
```

---

## 7. `evaluation_report.v1` (Evaluation layer)

**Required**: `schema, project, scope, target_ref, evaluator, evaluator_version, decision, blocking, findings, created`

`scope ∈ {artifact, run, benchmark, model_backend}`.
`decision ∈ {pass, warn, revise, block, fail}`. If `decision: block`, then
`blocking` must be `true`.

```yaml
---
schema: evaluation_report.v1
project: moe-pimc
scope: artifact
target_ref: idea/idea_proposal.v1.md
target_schema: proposal.v1
evaluator: contract.schema_validity
evaluator_version: 1
decision: pass
overall_score: 1.0
blocking: false
scores:
  schema_validity: 1.0
findings: []
recommended_actions: []
created: 2026-06-17T00:00:00Z
---
```

Evaluation reports are system artifacts. They should cite concrete
`evidence_refs` for every finding and are designed to feed HITL review,
feedback-loop routing, benchmark reporting, and future post-training exports.

---

## Schema compliance test set

`backend/tests/schema/test_schema_compliance.py` parametrizes ≥20 samples per schema (≥12 valid + ≥8 invalid). The aggregated valid-sample compliance rate is asserted to be ≥95% (matches PRODUCT.md §2 north-star indicator).
