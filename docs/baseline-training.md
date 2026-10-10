# Verify the baseline before optimization

A completed job means its declared computation finished. It does not mean the
model has converged. A short pipeline check must not become the reference for a
scientific optimization task.

The static trainer has two distinct launch contracts:

- `--max-steps N`: N optimizer updates. StepLR also advances after each update.
- `--max-iters N`: N full sweeps through the training batches. StepLR advances
  once per completed sweep. The configuration must allow at least N sweeps
  through `Etotal * Epoch`; the command is a cap, not a replacement for those
  loop limits.

Before execution, MARS shows the requested budget, configuration's full epoch
capacity and scheduler axis. Update-count runs carry an explicit warning. A
request exceeding the configured epoch capacity is blocked before launching.
The shared base configuration participates in the confirmation fingerprint, so
changing it requires renewed confirmation.

After execution, the adapter and result reader require the observed budget and
seed to match the approved job for both axes. A finite metric or a zero process
exit code alone cannot establish completion. Runs reporting less than one full
epoch are identified as short pipeline checks in result limitations.

For an established baseline, first compare code, complete configuration, data
hash, preprocessing, normalization, split and metric definitions against its
reference. Reevaluate a known checkpoint with strict state-dictionary loading
and the current evaluator before changing the model. Keep old records unchanged.

An optimization may start from a verified reference checkpoint, provided both
the control and candidate use that exact checkpoint, the same data and budget,
and an explicitly documented training protocol. A continuation experiment must
be labelled as continuation; it must not be described as training from scratch
or as an independently selected test result.
