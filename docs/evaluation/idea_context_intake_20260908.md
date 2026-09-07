# Idea input, progress and downstream handoff acceptance

## Delivered code

PR #14 integrated PR #13 and merged as `ce7dac7459b0e540bb02711415dd5b3947b60f1b`.
The merged tree `6d1e951c4fa93ac93ca5c2cd74910bb99503cf4c` equals the tested integration tree.

| User requirement | Implemented behavior |
|---|---|
| Supply research context | The new-task form and API use one `idea_context` contract: background, baseline_code, data_description, analysis_results, metric_definition and literature_notes. Scope and evaluation requirements remain explicit. |
| Preserve the actual inputs | New runs archive exact text with a checksum, reject missing/changed material, recover it after restart and carry it beside the complete approved proposal downstream. Historical options-only inputs retain their original Idea-only behavior. |
| Explain progress briefly | Initial progress confirms the material categories received. Research, candidate, validation and review events use concise human messages; candidate summaries remain explicitly unaccepted until validation finishes. |
| Deliver to the next Agent | The existing canonical proposal includes human_summary, method_spec and the versioned Experiment handoff, with resolving references, verification requirements and missing-context prerequisites. |
| Revise instead of losing the problem | Candidate-bound validation issues survive syntax repairs and tool observations. All declared budget cases and the controlled-comparison protocol are checked. |

## Verification

- 132 focused regressions passed after integration. The final progress change reran 29 input/delivery tests successfully; these are overlapping tests, not 161 distinct tests.
- The 11 new integration cases exercise real FastAPI requests, real temporary files, real context construction and recovery. No model/provider/tool substitute is used, and no model result is invented.
- Strict mypy passed for the six affected input/progress/Bridge/test files; frontend TypeScript passed; all four import-direction contracts passed.
- All four Core compatibility CI jobs passed on integration commit `e9a561898d125d50d7c06b086efc3b770c008983`, workflow run `34153199389`: backend/synthetic suite and typing, frontend production build, and both Windows script jobs.
- The cloud browser refused the local page with `ERR_BLOCKED_BY_CLIENT`; visual form interaction is not claimed. Local services were stopped after the attempted check.

## Real research remains below acceptance

These complete public-source evaluations ran the real DeepSeek V4 Flash/native ReAct loop. They use symbolic method scope and two public paper URL hints, without production PIMC code/data or GPU execution. Their loop fixes are included in the merged code; neither test is a live HTTP-to-model test of the new form.

| Run | Model calls | Actual tools | Outcome |
|---|---:|---:|---|
| `idea_lut_20260907T183449_cfa94f` | 13 | 10 | `protocol_exhausted`; two validation failures, five syntax failures, no accepted proposal. |
| `idea_lut_20260907T184549_2c8d97` | 17 | 12 | `reflection_rejected`; two protocol repairs, two validation repairs, three reviews. Structural/material checks passed, but the method remained rejected. |

The latter run took 624.74 seconds and reported 446,292 total tokens with all 17 requests/responses recorded. Its terminal checkpoint has no pending operation and no accepted model review. Remaining recorded blockers concern the internal-node interval convention and the ambiguous grid-movement metric: scaling every raw positive gap leaves the normalized grid unchanged, so raw-gap deviation cannot establish movement of the actual grid.

Review quality also remains a limitation. An earlier reviewer incorrectly claimed that sorting has zero gradient almost everywhere. A separate finite-difference check at distinct inputs `(0.3, 0.1, 0.7)` produced a nonzero permutation Jacobian. A second numerical check showed that sorting alone can produce duplicate grid nodes. These are bounded mathematical checks, not a PIMC simulation or a proof of overall proposal correctness.

No successful downstream delivery, scientific validation, measured gain or autonomous stability is claimed for these failed runs. A later targeted revision must preserve this failure and be reported separately, including any external assistance.

## Context needed for project-level acceptance

Supply the actual baseline repository/revision and relevant LUT implementation; the physical meanings and shapes of inputs/outputs; data splits and existing error/stability analyses; and the authoritative metric, target and parameter/resource limits. The new input path can carry these facts. Their availability will make project-level evaluation possible, but does not by itself resolve the remaining model-review and method-definition reliability problems.
