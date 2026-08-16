# V3.0 public release gate

The gate defaults to a dry run and accepts an explicit Git treeish. Its
allowlist is read from that resolved commit, and selected files are extracted
with `git archive`; uncommitted and untracked workspace data is never read as
release content.

```bash
python -m scripts.release.export_v30 \
  --treeish <full-commit> \
  --report-dir /tmp/mars-v30-release-evidence
```

The command exits non-zero for tree, history, secret, binary, internal-doc, or
Docker-context findings. It also fails when the external history scanner is
unavailable. `--gitleaks-if-installed` exists only for local development
evidence and is forbidden in release automation.

No archive is created unless `--materialize <new-path>` is explicitly supplied
and every gate passes. Existing archives are never overwritten. These tools do
not rewrite Git history, push changes, or delete local or remote refs.
