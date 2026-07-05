from __future__ import annotations

import json
from pathlib import Path

from app.harness.evaluation.run_report import evaluate_run_replay
from app.harness.evaluation.suite_report import SuiteTrialResult, write_suite_report
from app.harness.evaluation.suites import EvaluationSuite, ExpectedOutcome
from app.storage.run_store import RunStore


def test_write_suite_report_aggregates_trials_and_exports_self_evolution(tmp_path: Path) -> None:
    store = RunStore(runs_root=tmp_path / "runs")
    run = store.create(task="suite trial", project="pimc")
    suite = EvaluationSuite(
        id="suite_unit",
        expected=ExpectedOutcome(
            required_dirs=("input", "context", "idea", "events"),
            required_artifacts=("idea/missing.approved.md",),
            require_context_manifest=False,
            require_tool_audit=False,
        ),
    )
    evaluation = evaluate_run_replay(run=run, suite=suite)

    result = write_suite_report(
        suite=suite,
        trials=[SuiteTrialResult(run=run, evaluation=evaluation)],
        output_dir=tmp_path / "evaluation_runs" / "suite_unit",
    )

    assert result.report_json_path.exists()
    assert result.report_markdown_path.exists()
    assert result.scorecard_path.exists()
    assert result.self_evolution_export_path.exists()
    report = json.loads(result.report_json_path.read_text(encoding="utf-8"))
    assert report["trial_count"] == 1
    assert report["self_evolution_item_count"] >= 1
    assert "pass_power_k" in report
