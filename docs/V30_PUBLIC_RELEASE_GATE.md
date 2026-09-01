# MARS V3.0 public release gate

## Decision model

The release source must come from an explicit commit or immutable tag plus the
allowlist stored in that same commit. Dirty workspace files are out of scope by
construction. The default command is a dry run and fails closed.

Blocking checks cover:

- domain and organization denylist markers;
- user-specific absolute paths and secret-shaped values;
- binary artifacts and non-UTF-8 content;
- internal-only document paths;
- Docker build-context exclusions;
- reachable Git history and the external secret scanner.

Only a passing audit may materialize a new archive. The tooling refuses to
overwrite an existing archive and contains no history-rewrite, remote-push, or
remote-delete operation.

## Current status

The current Core tree is expected to remain blocked until legacy domain
fixtures, user-specific configuration, internal reports, and historical
material are migrated. W8 records that evidence; it does not edit those shared
modules.

Run:

```bash
python -m scripts.release.export_v30 \
  --treeish <full-commit> \
  --report-dir /tmp/mars-v30-release-evidence
```

`release_audit.json` is the machine-readable decision. `migration_plan.md`
lists every selected path with its blocking rules and repeats the committed
allowlist used for the audit.
