#!/usr/bin/env python
"""Replay-evaluate an existing MARS run and write a report."""
from __future__ import annotations

import argparse
from pathlib import Path

from app.harness.evaluation.run_report import evaluate_run_replay
from app.harness.evaluation.suites import load_suite
from app.settings import repo_root
from app.storage.run_store import RunStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="Run id under runs/")
    parser.add_argument(
        "--suite",
        default=None,
        help="Evaluation suite YAML path. Defaults to configs/evaluation_suites/mars_run_replay_v0.yaml.",
    )
    parser.add_argument(
        "--runs-root",
        default=None,
        help="Optional runs root, useful for tests or isolated workspaces.",
    )
    args = parser.parse_args()

    runs_root = Path(args.runs_root) if args.runs_root else repo_root() / "runs"
    run = RunStore(runs_root=runs_root).get(args.run_id)
    if run is None:
        parser.error(f"run not found: {args.run_id}")
    suite = load_suite(Path(args.suite)) if args.suite else load_suite()
    result = evaluate_run_replay(run=run, suite=suite)
    print(f"decision reports: {len(result.reports)}")
    print(result.markdown_report_path)
    print(result.scorecard_path)
    print(result.self_evolution_candidates_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
