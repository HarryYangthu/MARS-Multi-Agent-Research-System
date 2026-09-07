# Real Idea evaluation: a completed loop is not sufficient

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
