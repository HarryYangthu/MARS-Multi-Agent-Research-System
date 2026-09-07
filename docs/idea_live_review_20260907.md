# Real Idea evaluation: a completed loop is not sufficient

## Follow-up run: actual revisions still failed implementation review

Run `idea_lut_20260907T083733_656792` used source `0977032`, published as
`f2f2166` with an identical tree. It stopped honestly at `reflection_rejected`:
1,315.98 seconds, 15 model requests/responses/SDK attempts, 6 real tool calls,
2 protocol repairs, 1 schema/material repair and 3 rejecting Reflection rounds.
Reported usage was 190,841 input + 62,487 output = 253,328 tokens, complete.
Two PDFs were downloaded: PolyLUT and Free-Knots KAN. No final successful
artifact or experiment was reported.

Reflection did drive actual revisions of the cubic-spline ablation: first the
control count, then the explicit knot vector, then a remaining support-index
contradiction. However, independent inspection found additional missed issues:

- The selected output is complex, but the main real-scalar ledger says 289/324
  instead of 578/648. Its free-knot alternative adds 30 real coordinates to
  324 and is rejected; the consistent complex calculation is (648+30)/578,
  about 1.173, within this evaluation's 1.2 ratio.
- The initialization offers two competing procedures and promises a no-worse
  start in a non-nested function space without proof. Linearity in coefficients
  does not by itself establish conditioning or optimization stability.
- Squared complex differences need magnitude squares for a nonnegative real
  regularizer. Independent cell polynomials do not ensure C0 continuity.
- Decision thresholds leave uncovered cases, and one equal-budget comparison
  reverses the inequality used to reject the hypothesis.

Changes prepared from these failures: independently count typed parameter
tensor shapes; require two budget-feasible, typed alternative ledgers; keep
review issues pinned through format/schema repair; preserve an action receipt
index during context compression; request one statistical decision rule and
remove duplicate long method text. The next evaluation uses high effort for
both drafting and Reflection. These changes do not retroactively pass this run.

The native acceptance report now uses the actual invocation, candidate hash,
validation receipt, evidence and trace. It no longer requires an obsolete fixed
research recipe or GUI events for a headless evaluation.

## Earlier completed loop

Run `idea_lut_20260907T082315_8dd75f` used committed source `c7de8d9`,
published as GitHub checkpoint `a095ff6` (identical source tree). It used the
real Zhipu API and actual arXiv/PDF tools. No model, tool or service doubles ran.

Observed: 416.54 seconds; 8 model requests/responses and SDK attempts; 5 tool
calls/observations; 3 distinct arXiv results; one downloaded PolyLUT PDF and
two excerpt windows, with one cached-file reuse. Usage: 65,957 input + 11,511
output = 77,468 reported tokens. Schema and material checks passed. The model's
review first returned acceptance with three issues; protocol repair then
removed the issues and accepted the unchanged document.

Independent inspection rejected that candidate for implementation use:

1. Its node recurrence adds positive softplus increments but does not constrain
   their sum before a fixed final endpoint. With K=16, delta=0.001 and all
   interior logits zero, the last interior node is about 8.718, beyond endpoint
   1. Valid initialization does not guarantee valid training states.
2. Its declared x/y cell indexes and bilinear weights are transposed. With
   a 3-by-3 table containing row-major values 0..8, y at the lower boundary,
   the left and right limits at an x-cell boundary approach 1 and 3.
3. The warp map and the subsequent lookup coordinates have competing/vague
   definitions, including an undefined correction factor.
4. C0 continuity does not preserve complex phase equivariance. The proposal
   overstates its phase contract when describing separate real/imaginary LUTs.
5. It claims two debate rounds and a knowledge query that did not occur.
   Actual tools were local_docs, two arXiv searches and two PDF page reads.
6. The alternative methods are substantially the same over-budget polynomial
   construction, not two distinct feasible choices under the stated limit.
7. The equal-budget ablation labels compare the wrong pair, thresholds are
   inconsistent, and the proposed K=8 variant uses 77/64 > 1.2 parameters.
8. PDF download/extraction windows must not be described as complete page or
   full-document reading when returned excerpts were truncated.

The raw trace and original proposal remain unchanged in the run. The recorded
technical completion is not re-labelled as a scientifically validated result.

Changes made in response:

- A contradictory accepting review is conservatively routed to candidate
  revision, preserving every issue. Re-review keeps prior issues in context;
  re-submitting the identical candidate cannot bypass them.
- The Idea material gate rejects nonzero debate rounds without debate receipts.
- Method requirements and the review rubric now require endpoint/order,
  corner/boundary, phase, all-grid budget, and action-provenance checks.
- Reflection effort can be configured independently; this scenario uses high
  effort for review and low for research/drafting.
- The CPU reference loss reports the actual full residual objective. Artificial
  display ripple and expert/router-to-polynomial surrogates are removed.
- Bridge no longer silently completes an unregistered agent, and patch export
  copies an actual proposed diff instead of generating a fixed router patch.
- Model registry, loop and context tests were migrated individually to real
  configuration, registry, filesystem and serializer checks. Successful model
  repair remains a live-API requirement, not a canned test response.

The next real run must satisfy these checks and an independent implementation
review. Real PIMC data, baseline integration and GPU experiments remain outside
this supplied evaluation; no 2 dB performance claim is supported.

## Interrupted high-effort run and transport repair

Run `idea_lut_20260907T090254_d07f5a` started from clean source `f9623a0`
(published as `101d129`). Before any resumption it made 9 model requests,
received 8 model responses, performed 10 SDK attempts and 8 tool calls, and
failed with `model_error`. The ninth request received HTTP 502 twice, after
approximately 285 and 287 seconds. This is an observed upstream failure;
a non-streaming gateway deadline is a hypothesis, not a proven diagnosis.
Increasing the client timeout alone is not an established fix.

The actual tool history contains two empty memory queries, three arXiv searches
(10 distinct search sources), and three PDF fetch attempts. KAN and SineKAN
exceeded the configured 12 MiB limit. Free-Knots KAN was downloaded successfully
(2,442,172 bytes, SHA-256
`b35a6458203988c442468f0d8b3238dc2e9cf2629b25e8ab6af39ffdcf2a916b`).
Only pages 1–2 and a truncated excerpt of page 3 were visible to the model.
The run lasted 1,252.95 seconds; reported tokens total 69,858, excluding unknown
usage from the two failed SDK attempts. No candidate or Reflection was produced.

The Zhipu completion path now consumes the real SDK stream inside each bounded
retry attempt. A fresh accumulator isolates partial responses between attempts;
only visible output and usage are retained, with no private reasoning content.
Missing terminal markers, truncated output and empty content fail explicitly.
Public progress receipts contain chunk and visible-character counts.
Official interface reference: https://docs.bigmodel.cn/cn/guide/capabilities/streaming .

The live CLI now supports `--resume-run` for interrupted/model-error runs only.
It preserves original inputs, invocation, counters, remaining budgets and all
previous events. Before execution it snapshots the previous checkpoint, facts
and summary, hashes the old event prefix, and records the new source revision.
The audit checks these preserved files and lists every resumption source.
An exclusive filesystem lock rejects concurrent attempts; unknown tool outcomes
cannot be replayed automatically. Resumption is not a new clean trial.

Validation of this checkpoint: 64 targeted tests passed, including actual SDK
connection refusal followed by actual failed-request resumption, public stream
parsing, retry limits, Debate contracts, local trace redaction, real remote-worker
filesystem validation and real OpenSSH connection refusal. No successful remote
GPU or model response was invented. Targeted typing passed on the installed
Python 3.12 environment; Python 3.11 CI remains a separate gate.
