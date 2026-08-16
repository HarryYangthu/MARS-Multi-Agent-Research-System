# Synthetic regression Project Pack rules

- This pack is public, deterministic, and contains no external repository or
  dataset reference.
- The adapter uses only the Python standard library and never invokes a shell.
- Exactly twenty unique configurations are defined by the packaged search
  space; candidate identity is a hash of canonical configuration content.
- Synthetic metrics are regression-test evidence only, not scientific claims.
- Raw and canonical metric records must retain unit, direction, seed, dataset
  hash, evaluator hash, and candidate hash.
