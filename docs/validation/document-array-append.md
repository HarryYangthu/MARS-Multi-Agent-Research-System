# Explicit array append for document revision

A real Idea reflection required expanding the retrieved candidate list. The model
used out-of-bounds `set` operations and exhausted its protocol-repair allowance.
The revision tool now advertises a separate explicit `append` operation: its path
must identify an existing array and its value is supplied by the model.

Existing set/remove semantics are unchanged. Stale base hashes, missing parents,
non-array append targets and out-of-bounds set remain rejected. Changes are atomic
on a copy; schema, materials and independent review still validate the whole result.
The host neither supplies candidate content nor replays rejected operations.

Five pure regressions verify sequential appends, exact preservation of existing
content, caller/input immutability, invalid targets and stale/bounds rejection.
Existing field-revision contracts also passed. The actual UI retried the same
research task with its original cumulative 18 calls retained.
