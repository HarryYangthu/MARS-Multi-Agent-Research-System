# Phase 1 Status — Schema + Artifact + Run Lifecycle

## Sub-acceptance

| Item | Status | Evidence |
|---|---|---|
| 5 JSON Schema files | ✓ | `backend/app/harness/schema/schemas/{proposal,experiment_plan,code_spec,run_log,report}.v1.json` |
| `frontmatter_parser.py` (typed wrapper) | ✓ | `parse()` / `dumps()`; raises `FrontmatterError` on syntactic failure |
| `validator.py` (jsonschema) | ✓ | `validate_metadata()` / `validate_document()`; structured `ValidationError(path, message)` for UI highlight; coerces YAML datetimes → ISO strings |
| `storage/run_store.py` 9 subdirs | ✓ | `RUN_SUBDIRS = (input, context, idea, experiment, coding, execution, writing, hitl, events)`; auto-creates on `RunStore.create()` |
| `storage/artifact_store.py` versioning | ✓ | `<stem>.v1.md` / `v2.md` / `approved.md` rule; `latest()` prefers approved |
| `storage/file_store.py` uploads | ✓ | `workspace/uploads/<run_id>/` |
| `scripts/cli_validate.py` | ✓ | `python scripts/cli_validate.py templates/artifacts/*.md --task phase1_smoke` validates and writes all 5 |
| Schema test suite ≥20 / schema | ✓ | `backend/tests/schema/test_schema_compliance.py`: each schema gets ≥12 valid + ≥8 invalid permutations |
| Compliance rate ≥95% | ✓ | `test_compliance_rate_above_95_percent` asserts and passes |
| Template files validate | ✓ | `test_template_files_pass.py`: all 5 templates parse + validate |
| Artifact / run_store unit tests | ✓ | versioning, approval, listing, event jsonl writes |
| `mypy --strict` clean | ✓ | "Success: no issues found in 36 source files" |
| `lint-imports` 4/4 KEPT | ✓ | layered: api → bridge\|hitl → agents\|execution\|workers → storage → harness |
| End-to-end via CLI | ✓ | One command produces `runs/<id>/` with 9 subdirs and 5 valid `.v1.md` artifacts |

## Test counts

```
backend/tests/schema/test_schema_compliance.py  ≈ 100 parametrized cases (5 schemas × ≥20 samples + structural)
backend/tests/schema/test_template_files_pass.py   5
backend/tests/unit/test_artifact_store.py          4
backend/tests/unit/test_frontmatter_parser.py      4
backend/tests/unit/test_run_store.py               3
backend/tests/unit/test_validator.py               4
total: 127 passed
```

## How to verify

```
source .venv/bin/activate
PYTHONPATH=backend pytest backend/tests/schema/ backend/tests/unit/ -q
mypy --strict backend/app/
PYTHONPATH=backend lint-imports
python scripts/cli_validate.py templates/artifacts/proposal.v1.md --task phase1_smoke
ls runs/                                  # → fresh run dir with 9 subdirs
```

## End-to-end checkpoint (Phase 1)

E2E at this Phase = "human-authored md → schema validated → persisted into runs/<id>/".
The CLI run above demonstrates this. No agent / no LLM involved yet — by design.

## Notes / decisions

- **Layered architecture rev**: original draft put `harness | storage` as siblings; switched to `storage` above `harness` so `storage/artifact_store.py` can use `harness/schema/validator.py`. CLAUDE.md only forbids `harness/` from importing upward — does not forbid storage from depending on harness.
- **YAML-native datetime coercion**: PyYAML auto-parses unquoted ISO strings into `datetime.datetime` objects, which JSON Schema `type: string` rejects. Validator now coerces datetimes/dates to ISO via `_to_jsonable()` so both styles work.
- **`additionalProperties: true`** kept on every schema — frontmatter is a forward-compat surface; tightening would be a separate decision.
