# MARS Evaluation System Design

> Goal: turn today's scattered checks (schema, gates, metrics diagnosis, acceptance)
> into a systematic, auditable, project-aware evaluation layer.

## 1. Positioning

MARS 的评价体系不是一个单独的 Agent,也不是一个简单的分数面板。
它应该是 `harness/` 里的 agent-agnostic governance layer:

- `harness/evaluation/` 负责评估逻辑、scorecard、rubric、benchmark、证据引用。
- `bridge/` 只负责在正确时机调用评价,并把结果接入 RunGraph / HITL / feedback loop。
- `agents/` 不知道评价系统内部实现,只消费评价结果作为上下文或修订建议。
- `frontend` 只展示 scorecard、finding、evidence、趋势和 benchmark 对比。

评价结果必须像 Agent 输出一样可审计:结构化 frontmatter + markdown body,可版本化,
可被人编辑/确认,可进入后续 post-training 数据构造。

## 2. Existing Pieces To Unify

当前系统已经有评价材料,但分散在不同层:

| Existing piece | Current location | Unified role |
|---|---|---|
| Schema compliance | `harness/schema/`, `tests/schema/` | Contract evaluators |
| 5 HITL Gates | `harness/gates/`, `configs/gates.yaml` | Blocking policy checks |
| Execution metrics diagnosis | `bridge/diagnostics.py`, `projects/*/diagnostics.yaml` | Outcome evaluators |
| Baseline recall/precision | `tests/baseline/` | Benchmark evaluator |
| Acceptance harness | `scripts/acceptance.sh` | System regression suite |
| Idea quality rubric | `agents/idea/evals/quality_rubric.md` | Artifact rubric seed |
| Diagnosis artifact | `diagnosis.v1` | Existing evaluation-style artifact |

The design below keeps these assets, but registers them under one evaluation
model with consistent output, thresholds, evidence and routing semantics.

## 3. Evaluation Layers

Use five evaluation layers. The layers are ordered by blast radius: local checks
run early and cheaply; cross-run benchmarks run in CI or scheduled jobs.

| Layer | Name | Question answered | Example output |
|---|---|---|---|
| L0 | Contract | Is the artifact structurally valid and policy-safe? | schema errors, Gate 5 blockers |
| L1 | Artifact Quality | Is this specific Agent output useful enough for downstream work? | rubric scores and findings |
| L2 | Process Integrity | Did the run follow the intended lifecycle? | missing approval, skipped evidence |
| L3 | Research Outcome | Did the experiment meet project targets? | failed metrics, diagnosis, rerun target |
| L4 | Regression / Benchmark | Did the whole system improve or regress across a task set? | suite score, routing accuracy, cost |

Important rule: blockers do not get averaged away. A high average score cannot
hide schema failure, baseline violation, missing evidence, or a failed safety gate.

## 4. Evaluation Timing

Evaluation should happen at stable lifecycle points:

1. **Draft written**: run L0 + L1 before HITL review opens.
2. **Human approved**: snapshot the evaluation result for the approved artifact.
3. **Execution finished**: run L3 outcome evaluators and produce/refresh diagnosis.
4. **Run completed**: aggregate all layer results into a run scorecard.
5. **CI / scheduled benchmark**: replay fixed task suites through mock and selected real backends.

The current V0-compatible behavior is:

- Do not add a required tenth `runs/<id>/` subdir yet.
- Store per-artifact eval files beside the target artifact, e.g.
  `runs/<id>/idea/idea_proposal.v1.eval.md`.
- Store run-level scorecard as `runs/<id>/events/evaluation_scorecard.json`.
- V2 may add `runs/<id>/evaluation/` after `ACCEPTANCE.md` is updated.

## 5. Core Data Model

All evaluator output should be normalized to `evaluation_report.v1`.

```yaml
---
schema: evaluation_report.v1
project: pimc
scope: artifact          # artifact / run / benchmark / model_backend
target_ref: idea/idea_proposal.v1.md
target_schema: proposal.v1
evaluator: artifact_quality.idea_rubric
evaluator_version: 1
decision: revise         # pass / warn / revise / block / fail
overall_score: 0.78      # 0..1, null if pure blocker
blocking: false
scores:
  testability: 0.8
  evidence: 0.7
  downstream_readiness: 0.9
  baseline_safety: 1.0
  novelty: 0.5
findings:
  - id: F001
    severity: medium     # info / low / medium / high / blocker
    category: evidence
    message: "Novelty claim needs a concrete source or historical run comparison."
    evidence_refs:
      - idea/idea_proposal.v1.md#frontmatter.novelty
recommended_actions:
  - "Add one cited prior baseline and explain the delta."
created: 2026-06-17T00:00:00Z
---

# Evaluation Report

Human-readable rationale goes here. It should explain the score and link each
finding back to a concrete artifact, metric row, event, schema path or rule.
```

Required invariants:

- Every non-info finding has at least one `evidence_refs` entry.
- `decision=block` implies `blocking=true`.
- LLM-judge output is advisory unless a deterministic evaluator confirms the blocker.
- Evaluator version is mandatory so score drift is explainable.
- Scores are comparable only within the same evaluator/version.

## 6. Evaluator Types

### 6.1 Deterministic Evaluators

These run first and are trusted for blocking decisions:

- `contract.schema_validity`: frontmatter + JSON Schema.
- `contract.required_artifacts`: required upstream approved artifacts exist.
- `contract.project_policy`: project `AGENTS.md` static rules.
- `contract.baseline_compatibility`: Gate 5 dispatch-time compatibility.
- `process.run_completeness`: required run subdirs and event files.
- `outcome.metric_thresholds`: project metric targets from diagnostics config.
- `outcome.claim_metric_consistency`: report claims match `metrics.json`.

### 6.2 Rubric Evaluators

These score quality, usually with deterministic prechecks plus an optional LLM
judge. They should never import concrete Agent classes.

| Target | Rubric dimensions |
|---|---|
| `proposal.v1` | testability, evidence, downstream readiness, baseline safety, novelty |
| `experiment_plan.v1` | variable clarity, metric validity, ablation coverage, budget realism, baseline reuse |
| `code_spec.v1` | patch minimality, test adequacy, baseline preservation, risk clarity, dependency discipline |
| `run_log.v1` | metric completeness, reproducibility, failure isolation, curve/log integrity |
| `report.v1` | claim support, metric accuracy, limitation honesty, chain coverage, audience fit |
| `diagnosis.v1` | root-cause evidence, target selection, budget handling, action specificity |

### 6.3 Benchmark Evaluators

Benchmark evaluators operate across many runs:

- routing accuracy: user request -> correct entrypoint/stage.
- schema first-pass rate: valid first Agent output / total outputs.
- gate precision/recall: known fixture set.
- baseline match precision/recall: run_archive fixture set.
- e2e success rate: fixed task suite under mock mode.
- cost/latency budget: tokens, wall time, retry count, model backend.
- self-healing success: failed first attempt -> corrected rerun within budget.

## 7. Score Semantics

Use a two-part result: **decision** and **score**.

| Decision | Meaning | Pipeline effect |
|---|---|---|
| pass | Good enough to proceed | no interruption |
| warn | Proceed, but display findings | no interruption |
| revise | Needs human or Agent revision | opens HITL review with findings |
| block | Violates hard rule | blocks downstream transition |
| fail | Benchmark or completed run failed | marks suite/run as failed |

For artifact quality, recommended thresholds:

```yaml
pass: overall_score >= 0.80 and no high/blocker findings
warn: overall_score >= 0.65 and no blocker findings
revise: overall_score < 0.65 or any high finding
block: any deterministic blocker
```

For research outcome, project thresholds override generic thresholds. Example:

```yaml
pimc:
  metrics:
    loss:
      target: 0.04
      direction: lte
      aggregation: max
    RES:
      target: -43.5
      direction: gte
      aggregation: mean
```

## 8. Proposed Module Layout

```text
backend/app/harness/evaluation/
├─ __init__.py
├─ models.py                 # EvaluationReport, Score, Finding, Decision
├─ registry.py               # register evaluator by id/scope/schema
├─ runner.py                 # run evaluators for artifact/run/benchmark
├─ aggregation.py            # scorecard aggregation, blocker precedence
├─ artifacts.py              # write/read .eval.md and scorecard JSON
├─ evidence.py               # normalize artifact/event/metric refs
├─ rubrics.py                # load rubric configs, not Agent classes
├─ evaluators/
│  ├─ contract.py            # schema, required refs, project rules
│  ├─ artifact_quality.py    # schema-specific rubric evaluation
│  ├─ process_integrity.py   # RunGraph + HITL + event checks
│  ├─ research_outcome.py    # metrics + diagnosis bridge adapter
│  ├─ report_consistency.py  # claims vs metrics/evidence
│  └─ benchmark.py           # suite-level metrics
└─ prompts/
   ├─ artifact_quality_judge.md
   └─ report_consistency_judge.md
```

Dependency rules:

- `harness/evaluation/` may import `harness/schema`, `harness/kb`,
  `harness/gates`, `harness/runtime` data types, and storage primitives only if
  they remain agent-agnostic.
- It must not import `agents/`, `bridge/`, `api/`, or frontend code.
- `bridge/` may import `harness/evaluation.runner`.
- Existing `bridge/diagnostics.py` can be wrapped by `research_outcome.py` first,
  then gradually moved into harness if the bridge-specific parts are separated.

## 9. Configuration

Add `configs/evaluation.yaml` when implementation starts:

```yaml
version: 1

defaults:
  artifact_quality:
    enabled: true
    pass_threshold: 0.80
    warn_threshold: 0.65
    llm_judge: false
  benchmark:
    enabled: true
    mock_mode_required: true

scopes:
  artifact:
    enabled_evaluators:
      - contract.schema_validity
      - contract.required_artifacts
      - artifact_quality.rubric
  run:
    enabled_evaluators:
      - process.run_completeness
      - outcome.metric_thresholds
      - outcome.claim_metric_consistency
  benchmark:
    enabled_evaluators:
      - benchmark.schema_first_pass
      - benchmark.gate_precision_recall
      - benchmark.baseline_precision_recall
      - benchmark.e2e_mock_success

projects:
  pimc:
    metrics:
      loss: {target: 0.04, direction: lte, aggregation: max}
      RES: {target: -43.5, direction: gte, aggregation: mean}
    artifact_threshold_overrides:
      code_spec.v1:
        pass_threshold: 0.85
```

Rubrics can live in `configs/evaluation_rubrics/*.yaml` or be loaded from each
Agent context `evals/` directory. Config wins over prompt text.

## 10. Bridge Integration

Bridge should call evaluation at four points:

```text
Agent writes vN artifact
  -> EvaluationRunner.evaluate_artifact(ref)
  -> write <artifact>.eval.md
  -> if decision=block: node FAILED or waiting_review with blocker
  -> if decision=revise: HITL opens with findings
  -> else: normal HITL review

Artifact approved
  -> snapshot approved eval result

Execution completed
  -> evaluate_run_outcome(run)
  -> produce diagnosis.vN.md through existing BridgeAgent compatibility path
  -> if should_continue: feedback-loop decision

Run completed
  -> aggregate scorecard
  -> write events/evaluation_scorecard.json
```

This keeps the current "Execution miss -> Bridge diagnosis -> feedback loop"
behavior, but makes it one evaluator family inside the broader system.

## 11. UI Contract

Minimum UI surfaces:

- Run list: compact health badge (`pass`, `warn`, `revise`, `block`, `fail`).
- Run detail: scorecard tab with layer summary and latest findings.
- Artifact panel: eval badge beside each version, with "why" and evidence refs.
- Feedback loop modal: diagnosis + evaluation findings + recommended target.
- Benchmark page: suite history, backend comparison, regression trend.

The UI should never show only a bare score. It must show the decision, top
findings and evidence refs so the user can judge whether the evaluator is right.

## 12. Benchmark Suite Design

Create `evals/` or `backend/tests/evaluation/fixtures/` with fixed cases:

```text
evals/
├─ suites/
│  ├─ smoke_mock.yaml
│  ├─ pimc_routing.yaml
│  ├─ gate_regression.yaml
│  └─ baseline_reuse.yaml
└─ golden/
   ├─ proposal/
   ├─ experiment_plan/
   ├─ code_spec/
   ├─ run_log/
   └─ report/
```

Each case should define:

- user request / entrypoint
- optional uploaded artifacts
- expected schema
- expected route
- expected gate behavior
- expected metric thresholds
- expected minimum artifact scores
- allowed mock/real backend modes

The benchmark runner should emit:

- `benchmark_report.v1.md`
- `benchmark_results.json`
- per-case run refs
- regression summary versus previous baseline

## 13. Post-Training Boundary

V0/V0.5 evaluation produces data, but does not train.

Allowed now:

- export preference candidates from HITL edits and evaluator findings.
- compute composite labels for later use:
  `schema_validity`, `baseline_preservation`, `artifact_score`, `outcome_passed`.
- store evaluator version and evidence refs with every label.

Not allowed until V2:

- GRPO training loop.
- reward model training.
- live checkpoint routing.

This keeps the existing project boundary intact while making future reward
construction much less ad hoc.

## 14. Acceptance Criteria For The Evaluation System

Initial implementation is done when:

1. `evaluation_report.v1` schema exists and has >=20 schema tests.
2. `EvaluationRunner.evaluate_artifact()` runs L0 for all five existing schemas.
3. Current diagnosis logic is exposed as an outcome evaluator without changing behavior.
4. Every generated eval report has evidence refs for non-info findings.
5. No `harness/evaluation/` import violates architecture contracts.
6. Full mock pipeline still passes existing `scripts/acceptance.sh`.
7. A new benchmark command can run a small smoke suite and produce `benchmark_results.json`.
8. Frontend can display latest eval decision for an artifact or run.

## 15. Implementation Roadmap

### E0: Inventory Without Behavior Change

- Add `evaluation_report.v1` schema.
- Add `harness/evaluation/models.py`, `registry.py`, `runner.py`.
- Wrap schema validation and current diagnostics as evaluators.
- Write `.eval.md` beside artifacts, but do not block anything yet.

### E1: Artifact Rubrics

- Convert Idea rubric into structured YAML.
- Add rubrics for Experiment, Coding, Execution, Writing and Diagnosis.
- Add deterministic rubric checks first; optional LLM judge behind config flag.

### E2: Run Scorecard

- Aggregate artifact evals + diagnosis + HITL + event integrity.
- Write `events/evaluation_scorecard.json`.
- Add API endpoint and frontend scorecard tab.

### E3: Benchmark Runner

- Add fixed benchmark suite definitions.
- Reuse mock pipeline for deterministic CI.
- Track regression against checked-in golden expectations.

### E4: Feedback Loop Integration

- Map `revise` findings to HITL review comments.
- Let Bridge select repair target from evaluator categories.
- Keep human approval in the loop for non-auto mode.

### E5: Post-Training Export

- Export preference examples and composite labels.
- Keep all labels tied to evaluator versions and evidence refs.

## 16. Design Principles

- **Evidence before score**: no finding without traceable evidence.
- **Deterministic first**: LLM judges explain and rank; deterministic checks block.
- **Project-aware, platform-owned**: project thresholds live in project/config, but
  execution lives in harness.
- **No hidden coupling**: evaluators read artifacts and schemas, not Agent classes.
- **End-to-end first**: each phase must preserve the mock demo and current acceptance.
- **Human override is explicit**: HITL decisions are recorded, never silently erased.
