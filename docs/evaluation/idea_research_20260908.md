# Idea research development evaluation (2026-09-08)

The public symbolic evaluation used a 16×16 bilinear scalar LUT on [-1,1]², with a candidate limit of 256 trainable real scalars. No company code, project rules, private data, GPU simulation or measured PIMC gain was supplied or claimed.

## Implemented

The default Idea loop can delegate bounded research tasks to an independently configured Researcher. Own OpenAlex search and PDF reading tools produce traceable reports separating paper findings, proposed transfers and limitations. Reports link to the final method definition and Experiment handoff. Human-facing summaries remain short. Validation checks source identity, actual PDF reads, excerpts, parameter accounting and handoff references. Independent model review is a separate layer.

Fixes include explicit PDF-size budgets, per-subtask paper requirements while retaining global minima, actual title aliases, preserved reading-receipt metadata, gradual source-text compression and bounded repeated action descriptions. Review cannot silently accept missing required evidence.

## Results

Four complete real-model development runs were performed, plus one interrupted start with no response. These different development versions are not a frozen benchmark. The last complete run used 28 model calls and 24 tool dispatches, including a failed researcher. It retrieved 54 distinct sources, downloaded/read four PDFs in 15 windows, and accepted two papers into its research report. It passed schema, material checks and one independent-context model review. Own tools were used, not a literature MCP.

The four read papers were Instant neural graphics primitives with a multiresolution hash encoding; Spectral Tensor-Train Decomposition; Adaptive piecewise polynomial estimation via trend filtering; and Neural network approximation. The last two informed the accepted report. Selection followed gaps about fixed-budget representations and adaptive approximation. The 1D knot-selection and ReLU representation results motivate hypotheses; they do not establish a performance guarantee for a 2D LUT.

The proposed method replaces a fixed uniform 16×16 grid with 15×15 values and two axes of learnable node increments: 225+28=253 parameters. The expected benefit is allocating nodes toward local curvature, an untested hypothesis. It defines synthetic targets, training and ablations, but independent human inspection still found ambiguous boundary behavior, omitted accounting of initialization target-value queries, and a finite-precision spacing risk. Model-review acceptance is not scientific validation or readiness for unattended implementation.

The final context-compression fix was additionally validated by replaying actual failed context under its original budget and by regression/CI checks; it was not followed by another complete paid model run. The CLI used the authorized development Bridge bypass while invoking the actual IdeaAgent; UI and real-project execution are outside this test's scope.

Original proposals, PDFs, reading receipts, failed attempts and full traces are preserved separately for the project owner. GitHub carries the code and this public evaluation summary. Previously published evidence snapshots remain unchanged. No automatic downstream delivery or simulation occurred.
