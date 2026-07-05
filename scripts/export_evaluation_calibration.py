#!/usr/bin/env python
"""Export or score human calibration data for evaluation judges."""
from __future__ import annotations

import argparse
from pathlib import Path

from app.harness.evaluation.calibration import (
    export_human_calibration_samples,
    write_calibration_report,
)
from app.settings import repo_root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", action="append", default=[], help="Run id to sample. Repeatable.")
    parser.add_argument("--runs-root", default=None, help="Optional runs root.")
    parser.add_argument("--output", required=True, help="Output JSONL sample path or report JSON path.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--score-labels", default=None, help="Existing labeled JSONL to score instead of exporting samples.")
    args = parser.parse_args()

    output = Path(args.output)
    if args.score_labels:
        path = write_calibration_report(labels_path=Path(args.score_labels), output_path=output)
        print(path)
        return 0

    if not args.run_id:
        parser.error("provide at least one --run-id unless --score-labels is used")
    runs_root = Path(args.runs_root) if args.runs_root else repo_root() / "runs"
    run_roots = [runs_root / run_id for run_id in args.run_id]
    missing = [path.name for path in run_roots if not path.exists()]
    if missing:
        parser.error("missing run roots: " + ", ".join(missing))
    path = export_human_calibration_samples(
        run_roots=run_roots,
        output_path=output,
        limit=args.limit,
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
