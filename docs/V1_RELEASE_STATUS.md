# V1 Release Status

Last verified: 2026-06-17

## Gate Command

```bash
bash scripts/v1_release_check.sh
```

This delegates to `scripts/acceptance.sh`, which now covers:

- strict backend typing;
- import-linter architecture contracts;
- full backend unit and integration suites;
- schema, gate, baseline, tools V1, and context workbench checks;
- frontend typecheck, lint, and context workbench smoke;
- zero-external-dependency mock E2E demo;
- run directory completeness;
- tool audit / trace artifact checks;
- context manifest v2 coverage;
- posttrain CPU/mock dry-run.

## Latest Passing Run

- Demo run: `runs/2026-06-17T0854_acceptance_demo`
- Context manifests: 8
- Posttrain dry-run report:
  `posttrain/reports/2026-06-17T0854_acceptance_demo.20260617T085452Z.dry_run_report.json`
- Posttrain mock checkpoint:
  `posttrain/checkpoints/2026-06-17T0854_acceptance_demo.20260617T085452Z.mock_checkpoint.json`

## Notes

- External web-search smoke remains opt-in and is skipped by default.
- Frontend lint currently passes with warnings for existing `<img>` usage and
  hook dependency suggestions; they are non-blocking in the current gate.
- README/backend/frontend version labels remain `V0 / v0.1.0` until the product
  owner decides to promote the stable version label.
