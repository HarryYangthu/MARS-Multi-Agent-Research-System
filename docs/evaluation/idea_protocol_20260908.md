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

118 focused tests passed, including 22 parameter/audit regressions and 35 protocol tests, using arithmetic, schemas and real files. Strict mypy passed for 19 Idea/test source files. A pre-existing local mypy cache triggered an internal tool error on the first invocation; a separate cache completed normally. No provider/tool substitutes or mocked successful Agent execution were used.

## Fresh real evaluation

The fresh run is `idea_lut_20260907T183449_cfa94f` (UTC start date differs from the report's UTC+8 date). It started on clean local commit `c98b93d87d429291607e2745b78d815628bd6957`, tree `6942c307d455736ea203e0fa63e06288fb0f47b0`, equivalent to GitHub commit `f006cb40b88f46484b68fcd3f06de7d5e4cb1503`.

It uses the same public-source task and two paper URL hints as the preceding evaluation, a fresh isolated memory, DeepSeek V4 Flash, native ReAct tool actions and optional Reflection enabled for this test. No prior candidate or externally selected correction is supplied. Real baseline/data and GPU execution are outside this method-proposal test.

Execution is in progress. Final counts, artifacts, failure analysis and acceptance will be appended from the persisted trace; no success is asserted here.
