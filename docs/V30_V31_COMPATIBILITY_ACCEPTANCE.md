# MARS V3.0 / V3.1 compatibility acceptance

## Contract

- V3.0 Core owns versioned discovery records, distribution profiles,
  `project_pack.v1`, and `adapter.v1`.
- V3.1 is a composition profile over the same Core version. It may load private
  Packs; V3.0 rejects them. Both profiles load public Packs identically.
- Requests without V3.1 Idea options retain the V3.0 fast-path behavior.
- Core never imports a private Overlay. Packs and adapter packages enter through
  configured paths and normal Python installation or `PYTHONPATH`.

## Executable acceptance

```bash
PYTHONPATH=.:backend:posttrain/src:projects/synthetic_regression/src \
pytest backend/tests/e2e/discovery projects/synthetic_regression/tests -q

PYTHONPATH=.:backend:posttrain/src:projects/synthetic_regression/src \
python -m scripts.release.run_synthetic_smoke
```

Acceptance requires exactly twenty stable candidate IDs, complete raw and
canonical metric envelopes, non-empty Pareto output, and byte-equivalent public
Pack evaluation under the V3.0 and V3.1 profiles.

## CI evidence

The compatibility workflow runs backend tests, strict mypy, import boundaries,
frontend typecheck/build, and the twenty-candidate smoke. A dashboard or Pack
manifest without those command results is not implementation evidence.
