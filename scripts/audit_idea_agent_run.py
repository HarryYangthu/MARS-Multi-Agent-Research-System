#!/usr/bin/env python3
"""Regenerate and print the Idea Agent acceptance report for one run."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit a completed Idea Agent run and write idea_agent_acceptance_report.md."
    )
    parser.add_argument("run_id", help="Run id under runs/")
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=None,
        help="Optional alternate runs root.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Print the generated report to stdout.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from app.agents.idea.acceptance import write_idea_acceptance_report
    from app.storage.run_store import RunStore

    store = RunStore(runs_root=args.runs_root)
    run = store.get(args.run_id)
    if run is None:
        print(f"run not found: {args.run_id}", file=sys.stderr)
        return 2
    report_path = write_idea_acceptance_report(run=run)
    print(report_path)
    if args.print:
        print()
        print(report_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
