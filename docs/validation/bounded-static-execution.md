# Bounded static execution and result acceptance

The Test5 handoff requested 50 optimizer updates but its legacy code spec delivered
50 whole epochs, an ambiguous baseline seed and no per-job config bindings. The
host now rejects these mismatches before launch and supports explicit step budgets
only when the bound training entry actually declares `--max-steps`.

The adapter passes the approved budget as `--max-steps` or `--max-iters` without
conversion. Step runs must write a real summary proving the exact update count and
seed; missing summaries cannot become success from stdout alone. The external
research repository remains outside MARS and its changes pass ToolRegistry/Gate 5.

Observed step records are written during training, not after completion. TensorBoard
and the execution curve endpoint consume those values. Paper `RES` is the measured
residual/noise ratio in dB, not negative cancellation gain; the gain-derived linear
ratio has its own name. Training objectives and held-out RES have different units.

Users can choose “仿真完成后暂停，暂不生成报告” in the execution configuration
review. This boundary is persisted before launch, invalidates the old confirmation,
and stops dispatch after Execution reaches done, including after backend restart.
The Writing node stays pending. It also suppresses automatic evolution/diagnosis.

The current Test5 protocol retains its already approved legacy 0.9 two-way split,
512-sample ordered batches, seed 2026, 19,264 real-equivalent parameters and the
same capture. It is a bounded comparison under that historical protocol, not a
claim of independent final-test validation or of achieving the 2 dB target.

Validation uses actual files, real Git repositories, real numerical subprocesses
and source/schema functions; no provider or tool substitutes. The external research
suite passed 81 checks. The published-source MARS regression passed 203 tests
with one unavailable-checkpoint skip, including schema admission, approvals,
recovery, cancellation, execution and the durable reporting boundary.
TypeScript and strict typing are checked separately. Run results and screenshots
are recorded locally under the original run; no report-agent output is generated.

The results center now admits managed `paper_static` journals as well as local
command receipts. It checks saved input identity, attempt identity, receipt,
summary and log hashes, exact finite summary values, the approved budget and
seed, and all observed update/loss pairs. It reads only bounded files inside the
selected run and never recovers jobs, launches training, exports private logs,
or treats receipt consistency as independent reproduction. Backend measurement
units take precedence over legacy prose: APE here is cancellation gain in dB.

The actual Test5 batch completed all five jobs at 50 optimizer updates, seed 2026
and 19,264 parameters. All TensorBoard training/held-out scalars match the
flushed step observations, with two measured cancellation images per job. The
capture hash and frozen model/evaluation/data source files remain unchanged.
Baseline RES was 23.759658938 dB, combined RES 24.224086456 dB, loss-only RES
24.463686568 dB, scheduler-only RES 23.407894043 dB, and clip-only RES
23.759658938 dB. Best observed reduction was 0.351764895 dB; the 2 dB primary
goal was not met. This continuation reused existing approved research/design/
coding work with explicitly labelled, user-authorized handoff repairs; it does
not claim a fresh model-driven run of every upstream stage. Writing remains
pending, including after restart. Local evidence lives under
`runs/simulation-acceptance/`; external research code is not included here.
