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
suite passed 81 checks. MARS targeted checks passed 87 tests, including the native
submission fixture repair (required literature-quality fields are preserved).
TypeScript and strict typing are checked separately. Run results and screenshots
are recorded locally under the original run; no report-agent output is generated.
