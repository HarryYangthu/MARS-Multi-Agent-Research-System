# Repository read path regression

The test6 browser acceptance run exposed two failed `code.repo_reader` calls:
the Commander supplied absolute filenames inside the connected repository,
while the reader rejected every absolute path. The model recovered by retrying
relative paths, but the mismatch wasted a model call and repeated a prior issue.

The reader now normalizes absolute filenames only inside the active repository.
The existing resolver still enforces the frozen project read scope, repository
allowlist, ignored files and symlink containment. Mutation tools still require
relative paths. No model response, scientific evidence, or validation threshold
is rewritten.

Verification:

- 81 actual-file / pure-contract tests passed across repository path handling,
  native observations, Git research branches and frozen project scopes.
- Strict mypy passed for the changed code module.
- The actual test6 baseline configuration returned identical content, SHA-256,
  pagination and relative evidence references for relative and absolute reads.
- Regression cases reject external files, traversal, escaping symlinks, ignored
  repository directories and absolute-path writes.

This check establishes the read boundary. The full test6 research acceptance is
recorded separately in its desktop project; these tests are not research results.
