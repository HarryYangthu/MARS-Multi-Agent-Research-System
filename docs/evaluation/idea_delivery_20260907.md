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

The second fresh run and its measured result will be appended after execution. The first failure remains preserved in the evidence package.
