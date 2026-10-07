# Editable Office report exports and report skills

Validated on 2026-10-08 through the real MARS main chat, API, bridge and saved report artifacts. This checks report conversion and skill admission; it does not claim repository-wide bug freedom or new scientific acceptance.

## Real product verification

The already approved report for `2026-10-07T1221_static_pimc_lr_acceptance` was exported from the main chat. All three download controls were exercised. Browser downloads matched the fixed manifest's SHA-256 values exactly. The approved report hash remained `5ae6d9ce44b6c4749bccee5c21719d3c918f7e7b966f48001bbe546a27cfee85`; conversion did not call a model or rerun experiments.

- Excel reopened with 9 sheets, numeric experimental values, complete report text, source hashes and 4 native charts. Two recorded curve series and two receipt-bound 50-row training records were retained. Excel floating-point serialization was checked within its actual numeric precision; display rounding does not truncate source values.
- Word reopened with the full report, 77 paragraphs, 2 native tables and 1 saved experiment image. All 5 rendered pages were visually inspected for Chinese text, tables, wrapping and clipping.
- PPT retained 27 pages of report content, an editable native result table and the saved plot. All pages were rendered and visually inspected. OOXML package integrity and slide geometry checks reported no findings. Rendering uses a QA-only bundled LibreOffice and process-local system-font configuration, not a product runtime dependency. Native desktop PowerPoint was not separately exercised.

The example `evidence-report/SKILL.md` was imported through the browser, selected and saved for the project. A separate real isolated-runtime test built Writing Agent messages and confirmed imported instructions were present. No provider or tool response substitute was used. A sealed Writing admission test checked that the skill snapshot participates in the input fingerprint; restoring reads it without writing and detects tampering. Existing runs without the new snapshot retain their original context.

## Regression checks

The combined suite passed **99 tests**:

```sh
MARS_CODING_BACKEND=native_llm PYTHONPATH=backend .venv/bin/python -m pytest \
  backend/tests/unit/test_research_stage_service.py \
  backend/tests/unit/test_research_stage_runtime.py \
  backend/tests/unit/test_office_report_exports.py \
  backend/tests/unit/test_reporting_bundle_v2.py \
  backend/tests/unit/test_skills_registry.py -q --tb=short
```

The environment selection is for native contract-unit tests; it does not change the product's configured ZCode backend. Checks use actual temporary files, Office parsers, the skill registry and agent context construction, without model calls.

Twelve changed backend modules passed strict mypy with normal import stubs and `--follow-imports=silent`; frontend `npm run typecheck` passed. A final Office-only seven-test rerun passed after the workbook type correction.

Coverage includes full text beyond the former excerpt boundary, genuine Office structure, native charts/tables, numeric retention, formula-like text, missing approval, immutable generations, tampered/unlisted/traversal downloads, skill versions/tool permissions, malformed preference rejection, sealed admission/restoration and long slide headings without duplicate shapes. Main-chat report readiness now refreshes when Writing or run state changes, so approval does not require a browser reload to enable exports.

## Runtime and boundaries

The application installs `openpyxl` for editable workbooks/charts, `python-docx` for native report structure, `mistune` for Markdown parsing, and Pillow for plot proportions. `types-openpyxl` is a development-only typing dependency. PPT packaging uses portable OOXML and requires no desktop Office application or private Codex runtime.

Only approved schema-valid reports are final export sources. Each generation has its own Markdown snapshot, data pack, Office files and manifest hashes. Failed writers are marked failed rather than completed. Registered instructions do not grant tools, execute skill scripts or bypass report review. Scientific statements remain those of the approved report, and imported skills apply to subsequent new Writing admissions, not retroactively to already approved text.

Private research code/data, raw run artifacts, API credentials, imported user configuration and browser downloads are excluded from the source commit. Usage and format instructions are in [report-exports.md](../report-exports.md).
