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

Further fresh-run results will be appended after execution. All failures remain preserved in the evidence package.
