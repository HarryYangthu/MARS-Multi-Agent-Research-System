# Public CPU regression checks

This pack fits the packaged numerical dataset with actual ridge regression.
It is an engineering regression fixture, not a PIMC experiment or a substitute
for a real model provider. Only `mode: synthetic` is executable.

For each seed the sample indices are shuffled once. The last three samples are
the holdout. F0 fits six training samples and F1 fits eight, sharing the same
holdout; F2 is unsupported. Input normalization uses only the training samples.
The standard-library solver uses augmented least squares and reorthogonalized QR;
all polynomial coefficients, including the intercept, receive ridge regularization.

`validation_mse` is computed on the disjoint holdout and `model_terms` counts the
actual fitted coefficients. The legacy name `stability_score` is retained for
compatibility but now has an explicit definition: `1/(1+L2 coefficient norm)`
in normalized input coordinates. It is a coefficient-norm proxy, not a guarantee
of numerical, physical or statistical stability. Provenance records that definition,
the actual sample indices, and source/dataset/candidate hashes. No GPU is used.
