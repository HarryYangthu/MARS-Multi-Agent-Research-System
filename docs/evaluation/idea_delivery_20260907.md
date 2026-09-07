# Idea delivery real evaluation — 2026-09-07

The requested boundary is a research task and supplied context in, a short human summary plus a complete downstream proposal out. This evaluation uses real DeepSeek V4 Flash calls and public paper tools. There is no project baseline, private data or GPU execution. A method proposal passing review is not a measured PIMC improvement.

## First complete run: rejected

- Source: local commit `30ce749ddf48ccab76d1cbb3beaf38de60ffee50`, clean working tree.
- Run: `idea_lut_20260907T164948_8eb8a5`; invocation `cc8a1ced1e734f8daf05ad62ced55bbb`.
- Scenario: `configs/evaluation/idea_delivery_real.yaml`, ReAct actions plus separate reviewer context; two supplied public URL hints, fresh memory, 1.2 parameter-ratio evaluation assumption.
- Duration: 459.8 seconds. 12 model requests/responses and 12 actual research tool dispatches/observations. 244,414 total API-reported tokens; usage complete and trace counters consistent.
- Tools: 4 memory queries, 6 arXiv searches, 2 source-fetch calls. 13 distinct search hits, two downloaded PDFs, three page windows and one reuse of downloaded content.
- Read windows: arXiv `1907.02350v4`, pages 1–3 then 3–5; `2501.09283v1`, pages 1–3. The last page of each window was partly truncated; these are excerpts, not claims of complete reading.
- Outcome: `validation_exhausted`, five rejected candidates, zero reviewer calls. Four candidates failed the frontmatter boundary or YAML parsing. The fifth parsed but had 21 semantic delivery/material errors, including unresolved references and method/budget/decision definitions outside structured metadata. No accepted delivery was published.

## Changes driven by that failure

1. The model submits native JSON fields via `mars_submit_document(metadata, body)`. The host only serializes these fields; it does not supply scientific content or turn an invalid candidate into a success.
2. The generation schema explicitly requires the full method, decision rule and delivery fields; budget tasks also require budget, signal contract, alternatives and ablations. Host validation enforces this independently of provider behavior. Legacy documents retain their reader contract.
3. Submission is a loop control action, separate from research tool dispatches. A submission mixed with a research batch is rejected before any action executes. Trace records both the original call and serialized candidate digest.
4. Published artifacts preserve the exact accepted text, including trailing whitespace. Resumed invocations create a fresh export directory and preserve previous deliveries.
5. Supplied baseline context is archived with a hash and checked against actual model input for project-scope auditing. A generated declaration of code access is insufficient.

## Second complete run: rejected before tool execution

Run `idea_lut_20260907T170225_3d97fe` used clean local commit `a9522976844eb88d81fc3adb955144be818b3084`. It stopped after 21.2 seconds, five model calls and zero research tool dispatches, with 22,771 API-reported tokens. In each requested batch the model misspelled the opaque arXiv tool alias (omitting a zero). The whole invalid batch was rejected without executing or inventing observations. The generic error failed to identify the exact allowed name, so repeated repair attempts did not recover.

The adapter now uses readable provider-safe names, checks alias collisions before execution, and gives exact allowed names for unknown calls. It still rejects misspelled names instead of guessing a tool. The actual wire schema is part of the resume fingerprint, preventing an incompatible adapter version from replaying an old invocation.

## Third complete run: schema/material passed, review rejected

Run `idea_lut_20260907T170451_397567` used clean local commit `f62cebe943cb8e0bbb33b6d091d6ee2b56f94c57`. It took 487.9 seconds: 14 model requests/responses, 12 research tool calls, zero protocol repairs, two schema/material repairs and three model reviews. API usage was 325,523 tokens and trace counts were consistent. The candidate passed host schema/material validation before each review, but the loop ended `reflection_rejected`; no accepted delivery was published.

The new submission protocol removed the preamble/YAML failure. The remaining early repairs concerned numeric variable values, tensor shape arrays and exact parameter-count fields, so those requirements are now explicit in the submitted JSON Schema as well as the arithmetic checker.

The review found real definition gaps, including grid ordering, regularization and overlapping decision conditions. It also repeatedly demanded completed experiments, a theory of improvement and verification against unavailable production hardware from a method-only proposal. Some repeated issues contradicted its own acknowledgement that the candidate had already fixed a definition. The reviewer contract now distinguishes executable, falsifiable proposals from downstream experimental proof, requires current field-specific blockers, and allows erroneous prior review claims to be withdrawn with a reason. Genuine contradictions, missing definitions, false evidence and exaggerated guarantees remain blockers.

## Fourth complete run: review transport failed

Run `idea_lut_20260907T171439_e7bf4b` used clean local commit `7d101cf3198bbeb572729ad5c41de61b64c319b2`. It took 384.1 seconds: 14 model calls, 11 research tool dispatches, one arithmetic repair and one completed rejecting review. API usage was 336,153 tokens, complete, with consistent trace counters. The revised candidate reached host schema/material acceptance, but the next reviewer response contained leaked DSML tool-invocation text instead of review JSON; five protocol errors exhausted recovery. No accepted artifact was published.

This exposed incomplete context separation: although reviewer instructions differed, the reviewer still received the generator's native assistant/tool history while tools were disabled, and the candidate appeared as an assistant continuation. Review observations are now untrusted data documents, and candidates are user-provided documents in both phases. The protocol error branch now requests review JSON when reviewing instead of incorrectly requesting a tool call or proposal. No DSML fragment is interpreted as an executable tool call. The changed context format is bound into the resume fingerprint.

## Fifth complete run: interrupted, recovered, then review rejected

Run `idea_lut_20260907T172319_1c9c26` began on clean `51e923f4d387f4a325bf515c4457df03d5554c84`. The process disappeared while awaiting model request 4. An automatic approval review had questioned whether private repository content could be sent. Inspection of the persisted request confirmed public tools only, isolated memory, disabled project/repository context and no upstream artifacts. No private baseline was supplied.

Recovery exposed two actual CLI defects: a vanished process left status `running`, and serialized empty lists were compared directly with in-memory tuples. The explicit abandoned-request recovery now requires an exclusive run lock and a pending model request, rejects unknown tool outcomes, and preserves budgets. Prompt equality uses canonical serialized content. The invocation resumed on clean `6e10e3f27e6b1d04ebde3d7a1a59ec6e24245795` with a source journal; no counters or old evidence were replaced.

Final counts: 14 model requests, 13 responses, 12 research tools, one protocol repair, one validation repair, three reviews; terminal status `reflection_rejected`. API-reported usage is **at least 295,023 tokens**: the lost request makes usage incomplete. The summary's 339.1 seconds covers the resumed attempt only, not all elapsed time since initial launch. No accepted delivery was published.

The reviewer repeatedly copied stale complaints. Independent deterministic checks found the current structured Catmull-Rom basis/indexing and mirror mapping correct. However, the author adopted a false index explanation in the Markdown body, contradicting its correct structured formula. Actual remaining defects also included overlapping reject/inconclusive rules and an ablation that changed no behavior on the declared input domain. This was not a successful scientific review.

The next revision removes prior model criticism from fresh reviewer input while retaining it for the author. Reviewers must identify current fields and calculate counterexamples. New deliveries enforce a summary-only body, leaving a single structured method definition; legacy receipts retain their prior body policy. The author is instructed to prefer one justified core change and meaningful ablations. Context format changes remain resume-fingerprinted.

## Sixth complete run: delivery formatting passed, model review still rejected

Run `idea_lut_20260907T174135_95d606` used clean runtime commit `364dbec304db7fa07eb38ae228c0e8df8cb6cbbb`. It took 697.9 seconds: 17 model calls, 12 research tools, zero validation repairs, two protocol repairs and three reviews. API usage was 422,079 tokens, complete, with consistent trace counters. There were two memory calls, seven arXiv searches and three fetch calls: 21 distinct search results, two downloaded PDFs, four read windows and two cache reuses.

All three submitted candidates passed host validation immediately, including the summary-only body and structured handoff. The terminal status was still `reflection_rejected`, so no accepted delivery was published. The initial candidate had genuinely overlapping decision branches, ambiguous initialization wording and speculative domain expansion. The reviewer also raised false complaints about correct standard B-spline equations, contradicted its own arithmetic within an issue, and treated some acknowledged risks as missing implementation guarantees. One reviewer protocol error and one oversized author response required recovery. The final revision corrected the ordinary decision overlap but did not receive model acceptance.

Inspection of the actual phase configuration revealed that `reflection_reasoning_effort=high` inherited `thinking_enabled=false` from the native action model. The new independent `reflection_thinking_enabled` policy enables reasoning for the tool-free reviewer while retaining non-thinking native action calls. Both settings are recorded per request. This is a configuration fix being tested, not a claim that stronger self-review proves scientific correctness. The author is also told not to guess undefined domain acronyms, signal meanings or hardware.

## Seventh research run: a false acceptance, then a rejected assisted continuation

Run `idea_lut_20260907T175204_639c98`, invocation `b955a44840f34d45832fdf8918001255`, started on clean `3fbfa8e9a3648409e690cc6c55b4869a29a8b6b7`. Its initial attempt took 657.0 seconds, with 13 model requests, 12 tool dispatches and 313,879 API-reported tokens. The tool-free reviewer actually used thinking mode. One high-effort response had an extra JSON field and another was truncated; recovery with low effort produced an accepting review.

That acceptance was **wrong**. Independent checks found three material defects: a normalized-softplus gap formula did not guarantee its claimed minimum spacing; the declared training objective used held-out data; and a proposed `K=8` setting exceeded the parameter-ratio limit. The initial immutable export remains in the evidence archive but is not the final accepted result. Model acceptance is not a scientific correctness gate.

A candidate-digest-bound external review resumed the same invocation on clean `4ab13591e06c66911f69acd7f33b9704681c2dc1`, preserving counters, prior exports and the source journal. The model corrected those three defects. Its subsequent revisions and reviews still exposed reproducibility gaps: five training seeds had no stated source of randomness, and the baseline regularized objective was ambiguous. The parent invocation finally ended **`reflection_rejected`**, recorded as `failed`, with 17 model requests/responses, 12 actual tools, three protocol repairs, two validation repairs and three completed reviews. Cumulative active-attempt time was 939.5 seconds and usage was 441,522 tokens, complete. This failed parent must not be reported as a successful autonomous run.

The 12 tools were two memory queries, six arXiv searches and four source-fetch calls. There were nine distinct search results, two network PDF downloads, five excerpt windows and three cache reuses. The evaluation supplied two public URL hints, so this is not evidence of unseeded discovery. Actual downloads were:

| Paper | Why it was fetched | Recorded reading |
|---|---|---|
| Gradient-Adaptive Spline-Interpolated LUT Methods for Low-Complexity Digital Predistortion, arXiv `1907.02350v4` | LUT interpolation, region coordinates, control points and low-complexity adaptation | Pages 1–3, 4–7, then 1–3 again; pages 3 and 7 were partial |
| Free-Knots Kolmogorov-Arnold Network: On the Analysis of Spline Knots and Advancing Stability, arXiv `2501.09283v1` | Movable spline knots and their stability constraints | Pages 1–4 and 4–7; the first page-4 excerpt and page 7 were partial |

These receipts show excerpt reading, not full-paper reading or proof that the proposal follows correctly from either paper. Source PDFs and their recorded SHA-256 hashes were rechecked before final delivery.

## Final focused revision: deliverable with explicit external assistance

`idea_delivery_revision_20260907_01` ran on clean `7ef66746caae3401be743b5a2d4e50ae55024173`, source tree `e53775c2e0b4ccca9512c465df2438333e6d72b6`. It reused the parent's actual paper excerpts and candidate, with an exact-digest-bound review containing the final two recorded reviewer issues. Codex selected this bounded revision and provided the earlier independent feedback. No host-written solution or invented tool observation was supplied.

This is a separate, explicitly labeled revision, **not a new autonomous research run or a reset of the parent's exhausted review budget**. It needed one real DeepSeek V4 Flash request/response, zero new tools, zero protocol or validation repairs, and zero internal model reviews. Usage was 29,257 prompt tokens plus 5,993 completion tokens: 35,250 total, complete. The parent plus this revision used 18 model requests and 476,772 reported tokens; earlier failed runs are additional costs.

The model made the initialization randomness executable: each seed generates a control-point perturbation, shared between the baseline and candidate for that seed. Both now explicitly optimize `NMSE_train + lambda * R`, with `lambda=1e-3`. The final candidate preserves separate training/held-out data, the corrected positive-gap construction, and the two within-budget sizes. Independent bounded checks found no remaining blocker in those reviewed definitions; they are not a complete scientific proof.

| Acceptance layer | Final focused revision |
|---|---|
| Proposal Schema | Passed |
| Retrieved-material provenance and parameter arithmetic | Passed |
| Human summary, resolvable handoff references, required-context contract | Passed |
| Exact Markdown/JSON/summary/acceptance bundle | Passed |
| Trace consistency and source/candidate binding | Passed |
| Internal model review | Not run on this final revision; `model_review_passed=false` |
| External assistance | Present; `external_assistance=true` |
| Real project simulation / scientific performance validation | Not performed |
| Purely autonomous stable success | Not demonstrated |

The final proposal keeps the number of 2D LUT control points fixed and learns the per-axis grid locations. For `K=12`, the declared real-scalar parameter count is 144 to 166 (1.15278x); for `K=16`, 256 to 286 (1.11719x). The 1.2x ceiling is a test assumption, not a confirmed user requirement. Performance improvement remains a falsifiable hypothesis.

The exact model-written deliverable is preserved as [Markdown with YAML metadata](idea_delivery_proposal_20260907.md) and [JSON metadata](idea_delivery_proposal_20260907.json). Its application canonical digest is `c9e44355fa7e67729c076eaee9020b8995459fd7f3de4f0d38d65a5e7f56f807`; the raw Markdown SHA-256 is `e916d93edcbfa11c93e61d09be6f37cb4e88432954ea30b221c64e0047561602`. The evidence archive contains the exact export, acceptance receipt, source journal, all real traces and failures. Earlier accepted exports remain historical and must not replace this revision.

## Engineering checks and remaining boundary

The backend and synthetic-regression test suite passed, with eight environment-dependent skips; no mocked model/tool execution was used. Strict typing, four import-direction contracts and frontend type checking passed. GitHub Core compatibility CI passed at source-equivalent head `c3835dabeaea1873ae30f3d4fe0b8d0d9d738b9e`, run `34150774907`, including the frontend production build. Browser interaction and GPU execution were not tested.

The implemented boundary is a research task/context in, visible progress plus a schema-valid, evidence-bound handoff out. This test establishes a reviewed **method-level** example. It does not establish robust autonomous reviewer quality or success on real PIMC data. Before project execution, the handoff requests the real baseline revision/module, the meanings and shapes of the two LUT inputs/output, data and normalization, and the exact performance metric/threshold. Failures and missing context remain visible instead of being converted into successful simulation claims.
