# Idea end-to-end delivery follow-up — 2026-09-08

This change continues the research-task → readable summary + complete downstream proposal boundary. It does not add another fixed agent pipeline or claim measured PIMC improvement.

## Changes driven by real failures

| Observed gap in the preceding evaluation | Implemented change | Actual limit |
|---|---|---|
| The primary LUT size passed, while another proposed size exceeded the parameter limit | Evaluate every declared `parameter_budget.evaluation_cases` assignment with the same formulas, typed parameter tensors and host limit | Dimensions mentioned only in prose cannot be inferred reliably; the model must use the canonical list |
| The training objective referred to held-out data | `evaluation_protocol` declares dataset roles and training-objective data references; held-out references are rejected in training | Declarations do not prove physical sample disjointness |
| Baseline and candidate objectives were ambiguous | Each arm references one canonical objective; architecture isolation requires the same objective and training data | Intentional non-architecture differences remain allowed with justification |
| Multiple seeds had no defined random process | Unique explicit seeds and method-bound random-source definitions | The checker cannot prove an implementation consumes that source |
| Downstream needed to reconstruct definitions from scattered prose | Full proposal carries the canonical protocol and resolving method references | The human summary alone is insufficient for execution |

New parameter-budget tasks require both fields. Other comparison tasks can request the protocol separately. Validation receipts bind the enforced requirements to the candidate so later auditing cannot omit them. Legacy artifacts retain their historical contract. The checks are ordinary framework-independent Python/JSON Schema functions; the native ReAct executor and optional Reflection interface are unchanged.

## Engineering validation

152 focused tests passed, including 22 parameter/audit regressions, 35 protocol tests and 34 API/Bridge input tests, using arithmetic, schemas and real files. Strict mypy passed for 19 Idea/test source files. A pre-existing local mypy cache triggered an internal tool error on the first invocation; a separate cache completed normally. No provider/tool substitutes or mocked successful Agent execution were used.

## Fresh real evaluation

The fresh run is `idea_lut_20260907T183449_cfa94f` (UTC start date differs from the report's UTC+8 date). It started on clean local commit `c98b93d87d429291607e2745b78d815628bd6957`, tree `6942c307d455736ea203e0fa63e06288fb0f47b0`, equivalent to GitHub commit `f006cb40b88f46484b68fcd3f06de7d5e4cb1503`.

It uses the same public-source task and two paper URL hints as the preceding evaluation, a fresh isolated memory, DeepSeek V4 Flash, native ReAct tool actions and optional Reflection enabled for this test. No prior candidate or externally selected correction is supplied. Real baseline/data and GPU execution are outside this method-proposal test.

The run failed with `protocol_exhausted`: 13 real model calls, 10 tool calls, 2 submitted candidates, 2 validation failures and 5 syntax failures; 492.96 seconds, 324,137 reported tokens. It downloaded two papers. The first submission had four host errors, including a shared train/held-out specification and incorrect alternative parameter counts. The second still had two errors. Subsequent native arguments repeatedly appended another `body` outside the closed root object. No candidate passed, no Reflection acceptance occurred, and no experiment ran.

The failure exposed a generic executor defect: a protocol error or successful tool observation overwrote pending candidate validation feedback. Validation issues now persist separately, are bound to the current candidate digest in packed context, and are replaced only on a newly validated candidate. They are excluded from fresh reviewer context. Native syntax repair now explicitly describes the metadata/body root boundaries; no invalid model output is automatically repaired or accepted. 62 loop/protocol tests passed (including five new pure context regressions), and strict mypy passed for the three changed loop/test files.

The production API also now accepts labeled Idea context and typed requirements. The actual persisted run options are consumed by the Idea-stage Bridge loader; exact caller text is retained. This path is verified with real temporary archives and payload validation, not a live HTTP-to-model integration test.

A fresh evaluation on the repaired executor follows; its result will be recorded separately rather than overwriting this failed trace.


## Repaired-loop fresh run

`idea_lut_20260907T184549_2c8d97` ran on clean local `f00547865aea9f7b5aa9fa3c21b08c66d2f7a0f5`, tree `89848fc36087ca4e14673f00760e1bd471f9336c` (remote `71ff7b6083db338dd313a182a521d58ac25028c4`). CI run 34152943257 passed.

It ended `reflection_rejected`, not accepted: 17 model calls, 12 real tools, two protocol repairs (including output-length recovery), two candidate-validation failures, and three completed reviews. Runtime 624.74 seconds; 446,292 reported tokens, complete. The author independently repaired dataset references, symbolic tensor shapes and an over-budget small grid. Subsequent candidates passed host structure/material checks, but no final delivery was published because the last reviewer still required an internal-node tie convention and a normalized grid-movement metric.

The repaired loop demonstrably retained validation errors through a subsequent syntax failure (`validation_issues_visible=true` in the trace), even while older observations were compressed. The original failure and this run remain separate and neither is counted as success.

Review quality still has limitations. The first review incorrectly claimed sorted values have almost-everywhere zero gradient: at distinct inputs their local Jacobian is a permutation matrix, confirmed with finite differences. Independent inspection also found that the hard-nearest-neighbor free-grid ablation does not update node locations through the stated Adam/backpropagation path. The actor and reviewer instructions now explicitly trace loss-to-parameter paths and distinguish hard indices from sorted values. This is an instruction improvement, not a proven mathematical verifier.

## Explicit assisted revision

A reusable headless `scripts/revise_idea_candidate.py` now supports a separate, candidate-digest-bound revision using an audited terminal parent. It preserves the parent unchanged, records external assistance, runs no new research tools, reuses only historical evidence, and validates the complete output with IdeaAgent. Five pure lineage tests passed; total focused tests across this continuation are 219, with strict type checks on changed files.

The first revision preparation (`idea_delivery_revision_20260908_01`) exceeded the pinned context budget before any model call. The script now uses the shared context packer for historical evidence. A second preparation/run uses clean local `08954ec029c975ddc6c9419ea88a4f2657d9b655`, tree `f24f30b7635f55b13cec9c9367634e8a01f9c1ae`, remote `b0ccdfc24508fa8cd24c8306d89cc8fc17218eea`. It includes the two recorded reviewer issues plus the independent ablation trainability issue, so it must not be called unassisted end-to-end success.


The second assisted revision was blocked by automatic approval review, which classified transmission of candidate material and historical trace context to DeepSeek as requiring explicit data-export authorization. Local read-only inspection confirmed the parent was `public_research`, project rules/code loading were disabled, and upstream artifacts were empty. Nevertheless the execution session became unavailable after the rejection. Its last checkpoint records one pending model request and SDK attempt, zero responses; completion and usage cannot be established. No success is attributed to this revision, and its parent remains failed. The incomplete checkpoint is preserved unchanged, with a separate interruption note.

### Current acceptance

- Engineering: parameter/protocol/context/feedback changes passed targeted tests and strict type checks; source changes are checkpointed remotely.
- Autonomous method delivery: **not yet passed** in either fresh run of this continuation. Candidate host checks improved, but the final model review did not accept.
- Assisted revised delivery: **blocked**, no confirmed output.
- PIMC simulation, 2 dB performance, GPU usage and stable autonomous quality: **not demonstrated**.

To continue the blocked revision, clarify approval for sending the selected candidate and its historical public-paper tool observations to DeepSeek. Preserve the uncertain pending invocation; do not silently replay it or count it as a completed request.
