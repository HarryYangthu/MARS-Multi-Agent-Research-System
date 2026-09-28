# V3.0 public release gate

The gate defaults to a dry run and accepts an explicit Git treeish. Its exact
file manifest, `v30_tree_allowlist.txt`, is read from that resolved commit.
Each non-comment line names one reviewed file; directory entries, wildcards,
negative entries, duplicate entries and missing files fail closed. Literal
Next.js paths such as `frontend/src/app/runs/[id]/page.tsx` are supported.
New files require an explicit manifest change before they enter an export.

Selected files are extracted with literal-pathspec `git archive`; uncommitted
and untracked workspace data is never read as release content. Credentials,
user-state directories and generated caches are forbidden even if explicitly
added to the manifest. The source manifest keeps dynamically loaded schemas,
prompts, skill instructions, artifact templates, and the native Windows
entrypoints with their direct dependencies. Historical documentation, tests,
developer scripts, Docker launchers and training output are not selected.

```bash
python -m scripts.release.export_v30 \
  --treeish <full-commit> \
  --report-dir /tmp/mars-v30-release-evidence
```

The command exits non-zero for tree, history, secret, binary, internal-doc, or
Docker-context findings. It also fails when the external history scanner is
unavailable. `--gitleaks-if-installed` exists only for local development
evidence and is forbidden in release automation. Even with that flag,
materialization requires a successful real gitleaks scan.

No archive is created unless `--materialize <new-path>` is explicitly supplied
and every gate passes. Existing archives are never overwritten. These tools do
not rewrite Git history, push changes, or delete local or remote refs.

## Runtime seed assets

`runtime_assets.txt` lists the exact non-Python resource subset used when
initializing an isolated application data directory. Its entries must also
appear in the source manifest. It contains shipped defaults, schemas, context
resources, templates and public synthetic-project metadata. User credentials,
existing runs, external code and real datasets are never seed inputs. Python
modules and the synthetic evaluator package must be installed separately.
This list does not authorize overwriting user-edited configuration on upgrade.

## Current acceptance boundary

This command builds a reviewed **source** snapshot, not a signed desktop
installer. The repository cleanup inventory records remaining domain-specific
defaults, personal-page assets and platform validation work. Required runtime
dependencies are retained until their callers are migrated; retaining them in
the manifest does not bypass content findings. A blocked audit produces no
archive. Do not describe successful selection, unit tests or a desktop startup
spike as public-release acceptance.
