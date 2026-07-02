#!/usr/bin/env python3
"""Validate one or more markdown files and write the valid ones into a run.

Usage:
    python scripts/cli_validate.py <md-file> [<md-file> ...] [--task TASK]
    python scripts/cli_validate.py --check <md-file>      # just validate, do not write
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.storage.artifact_store import (  # noqa: E402
    ArtifactStore,
    ArtifactValidationError,
)
from app.storage.run_store import RunStore  # noqa: E402
from app.harness.schema.validator import validate_document  # noqa: E402


def _check_only(paths: list[Path]) -> int:
    failures = 0
    for p in paths:
        text = p.read_text(encoding="utf-8")
        result = validate_document(text)
        if result.valid:
            print(f"[OK]  {p}  ({result.schema_id})")
        else:
            failures += 1
            print(f"[ERR] {p}  ({result.schema_id})")
            for e in result.errors:
                print(f"      {e.path}: {e.message}")
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate MARS markdown artifacts")
    parser.add_argument("paths", nargs="+", type=Path, help="markdown files to validate")
    parser.add_argument("--task", default="cli_validate", help="task slug for the run")
    parser.add_argument(
        "--project",
        default="pimc",
        help="project name (must match frontmatter project)",
    )
    parser.add_argument(
        "--check", action="store_true", help="validate only, do not write into runs/"
    )
    args = parser.parse_args(argv)

    paths: list[Path] = [p.expanduser().resolve() for p in args.paths]
    for p in paths:
        if not p.exists():
            print(f"[ERR] file not found: {p}", file=sys.stderr)
            return 2

    if args.check:
        return _check_only(paths)

    store = RunStore()
    run = store.create(task=args.task, project=args.project, entrypoint="cli")
    art_store = ArtifactStore(run)
    print(f"[run] {run.run_id} -> {run.root}")

    failures = 0
    for p in paths:
        text = p.read_text(encoding="utf-8")
        try:
            ref = art_store.write(text=text)
        except ArtifactValidationError as exc:
            failures += 1
            print(f"[ERR] {p}: {exc}")
            for e in exc.result.errors:
                print(f"      {e.path}: {e.message}")
            continue
        print(f"[wrote] {ref.path.relative_to(REPO_ROOT)}")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
