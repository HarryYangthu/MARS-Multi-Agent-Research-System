from __future__ import annotations

import json
from pathlib import Path

from app.harness.evaluation.calibration import (
    export_human_calibration_samples,
    write_calibration_report,
)
from app.harness.evaluation.run_report import evaluate_run_replay
from app.harness.evaluation.suites import EvaluationSuite, ExpectedOutcome
from app.storage.run_store import RunStore


def test_human_calibration_export_and_report(tmp_path: Path) -> None:
    run = RunStore(runs_root=tmp_path / "runs").create(task="calibration", project="pimc")
    suite = EvaluationSuite(
        id="calibration",
        expected=ExpectedOutcome(
            required_dirs=("input", "context", "events"),
            required_artifacts=("idea/missing.approved.md",),
            require_context_manifest=False,
            require_tool_audit=False,
        ),
    )
    evaluate_run_replay(run=run, suite=suite)
    samples_path = export_human_calibration_samples(
        run_roots=[run.root],
        output_path=tmp_path / "samples.jsonl",
    )
    samples = [
        json.loads(line)
        for line in samples_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert samples
    samples[0]["human_decision"] = "pass"
    labels_path = tmp_path / "labels.jsonl"
    labels_path.write_text(
        "\n".join(json.dumps(sample, ensure_ascii=False) for sample in samples),
        encoding="utf-8",
    )
    report_path = write_calibration_report(
        labels_path=labels_path,
        output_path=tmp_path / "calibration_report.json",
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema"] == "evaluation_calibration_report.v1"
    assert report["comparable_count"] >= 1
