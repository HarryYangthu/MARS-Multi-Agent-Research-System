# Independent review of the unaccepted Idea draft

The previous run failed format validation. This is revision feedback, not a scientific acceptance certificate. Use only the already retrieved public papers and receipts. Do not invent experiments or new source readings. Return a compact complete document, no introductory explanation or fences. Keep all definitions in metadata and body under 600 Chinese characters.

Resolve these concrete defects throughout the document, not by adding a contradictory disclaimer:

1. C2 regularity alone does not justify fourth-order cubic-spline approximation. State adequate derivative/mesh assumptions or remove the order guarantee. An asymptotic rate does not establish a finite-budget accuracy gain.
2. For clamped cubic B-splines specify zero-denominator terms in Cox-de Boor, the right endpoint basis convention, and dimensionally consistent coordinates (normalized coordinates vs physical Amax knots).
3. In bilinear interpolation floor at the maximum input leads to an out-of-range i+1. Define the boundary cell index and its interpolation fraction explicitly.
4. Normalized eps+softplus gaps do not ensure a fixed minimum normalized interval: the denominator can become arbitrarily large. Address representable numerical ordering, finite extreme logits and fixed endpoints; do not assert eps is the final normalized lower bound.
5. Unconstrained real coefficients do not ensure nonnegative gain. Correct the sign/phase contract and define x_ref from provided inputs or declare it a separate required input. Handle zero output and reference phase explicitly.
6. The alternative N=17 uses N*N with global N=16, contradicting shape [17,17] and count 289. Use consistent distinct variables and count all trainables.
7. For fixed degree and M=12 control points, the knot-vector length and interior-knot count are constrained. The G=9 vs G=5 ablation cannot hold those unchanged. Define mathematically valid controls and distinguish equal-control-point vs equal-total-parameter comparisons.
8. Sampling a baseline at control-point locations is not identical to least-squares projection. Give one reproducible initialization: sampling locations, target, solver and explicit fit-error handling. Do not promise a no-worse initialization for non-nested spaces.
9. A theorem about effective knots in a composed KAN is not automatically a theorem for a single 2D tensor-product LUT. Separate each paper's findings from this proposal's transfer hypotheses.
10. Keep one metric direction and one exhaustive decision rule; specify the statistical resampling unit without treating correlated blocks as independent replicates.

The proposal may remain a falsifiable method hypothesis. No production baseline, dataset or GPU has been supplied; no performance or novelty claim is experimentally established.
