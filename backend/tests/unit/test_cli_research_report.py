"""Pure evidence parsing/rendering contracts with actual temporary files.

These hand-authored records are arithmetic/layout test inputs. No provider,
research tool or simulation is replaced, and no scientific result is asserted.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from app.bridge.cli_research_report import collect_evidence, write_report
from app.harness.agent_loop.trace import atomic_json
from app.harness.schema.validator import validate_document


def _manifest() -> dict[str, Any]:
    return {"project": "report-contract", "task": "Verify persisted evidence rendering", "data": "/absent/capture.pth",
            "budget": {"max_steps": 3, "rounds": 1, "reduction": .2, "max_degradation_db": 0, "timeout_seconds": 60}}


def _lines(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_prepared_report_has_no_invented_measurements_or_charts(tmp_path: Path) -> None:
    state = {"status": "blocked_data", "error": "Dataset missing", "trials": {}, "artifacts": {}}
    write_report(tmp_path, _manifest(), state)
    report = (tmp_path / "report.md").read_text()
    assert validate_document(report, expected_schema="report.v1").valid
    assert "blocked_data" in report and "Dataset missing" in report
    evidence = json.loads((tmp_path / "evidence/summary.json").read_text())
    assert evidence["charts"] == []
    assert evidence["trials"] == []
    assert evidence["acceptance"]["engineering_complete"] is False
    assert evidence["acceptance"]["scientific_goal_met"] is None
    assert evidence["resource_usage"]["usage_complete"] is False
    with (tmp_path / "evidence/trials.csv").open(newline="") as stream:
        assert list(csv.DictReader(stream)) == []


def test_trace_totals_do_not_double_count_ledger_or_hide_unknown_usage(tmp_path: Path) -> None:
    trace = tmp_path / "stages/idea/proposal/agent_traces/idea/attempt1"
    _lines(trace / "events.jsonl", [
        {"kind": "model_request", "request": 1, "model": "contract", "time": "2026-01-01T00:00:00Z"},
        {"kind": "sdk_attempt_started", "request": 1, "time": "2026-01-01T00:00:00Z"},
        {"kind": "model_response", "request": 1, "time": "2026-01-01T00:00:02Z",
         "usage": {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20}},
        {"kind": "tool_dispatch", "step": 1, "tool": "contract-parser", "time": "2026-01-01T00:00:02Z"},
        {"kind": "observation", "step": 1, "tool": "contract-parser", "ok": False, "time": "2026-01-01T00:00:03Z"},
        {"kind": "model_request", "request": 2, "model": "contract", "time": "2026-01-01T00:00:03Z"},
        {"kind": "sdk_attempt_started", "request": 2, "time": "2026-01-01T00:00:03Z"},
        {"kind": "sdk_attempt_failed", "request": 2, "time": "2026-01-01T00:00:04Z"},
        {"kind": "model_error", "time": "2026-01-01T00:00:04Z"},
    ])
    atomic_json(trace / "facts.json", {"status": "model_error", "usage_complete": False,
                "counts": {"model_requests": 2, "model_responses": 1, "sdk_attempts": 2, "tool_dispatches": 1},
                "usage": {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20}})
    atomic_json(tmp_path / "resources/model_budget.v1.json", {"configuration": {"limits": {"max_model_requests": 2}},
                "requests": {"unknown": {"status": "failed", "charged_tokens": 9000, "usage": None,
                                            "charged_cost": None, "usage_complete": False}}})
    evidence = collect_evidence(tmp_path, _manifest(), {"status": "failed", "trials": {}})
    usage = evidence["resource_usage"]
    assert usage["total_tokens"] == 20
    assert usage["model_requests"] == 2 and usage["model_responses"] == 1
    assert usage["sdk_failures"] == usage["tool_failures"] == 1
    assert usage["usage_complete"] is False
    assert evidence["model_calls"][1]["prompt_tokens"] is None
    assert evidence["model_calls"][1]["status"] == "failed"
    assert evidence["model_calls"][0]["elapsed_seconds"] == 2
    assert evidence["tools"][0]["elapsed_seconds"] == 1
    assert evidence["model_budgets"][0]["requests"][0]["charged_tokens"] == 9000


def test_partial_files_and_stale_summary_are_reported(tmp_path: Path) -> None:
    trace = tmp_path / "stages/idea/proposal/agent_traces/idea/attempt1"
    _lines(trace / "events.jsonl", [{"kind": "model_request", "request": 1}])
    with (trace / "events.jsonl").open("a") as stream:
        stream.write('{"kind":')
    atomic_json(trace / "facts.json", {"counts": {"model_requests": 0}, "usage_complete": False})
    evidence = collect_evidence(tmp_path, _manifest(), {"status": "interrupted"})
    assert evidence["resource_usage"]["model_requests"] == 1
    assert len(evidence["warnings"]) == 2
    assert "Incomplete/corrupt" in evidence["warnings"][0]
    assert "counters differ" in evidence["warnings"][1]
    write_report(tmp_path, _manifest(), {"status": "interrupted"})
    assert "证据读取警告" in (tmp_path / "report.md").read_text()


def test_exact_metrics_and_actual_step_axes_are_exported(tmp_path: Path) -> None:
    output = tmp_path / "execution/baseline/attempt_01"
    _lines(output / "history.jsonl", [
        {"epoch": 0, "optimizer_steps": 0, "validation": {"RES_db": 9.0}},
        {"epoch": 1, "optimizer_steps": 3, "validation": {"RES_db": 2.0}},
    ])
    _lines(output / "steps.jsonl", [{"epoch": 1, "optimizer_step": step, "training_loss": 4.0 / step} for step in (1, 2, 3)])
    trial = {"status": "completed", "operation": "train", "real_parameters": 100,
             "validation": {"RES_db": 2.0, "per_channel_db": [0.0, 3.0]},
             "optimizer_steps": 3, "selected_optimizer_steps": 3, "elapsed_seconds": 1.5, "output": str(output)}
    atomic_json(output / "result.json", trial)
    state = {"status": "experimenting", "trials": {"baseline": trial}, "artifacts": {}}
    write_report(tmp_path, _manifest(), state)
    evidence = json.loads((tmp_path / "evidence/summary.json").read_text())
    assert evidence["trials"][0]["validation_RES_db"] == 2.0
    assert evidence["trials"][0]["test_RES_db"] is None
    assert evidence["channels"] == [{"trial": "baseline", "split": "validation", "channel": 1, "RES_db": 0.0},
                                    {"trial": "baseline", "split": "validation", "channel": 2, "RES_db": 3.0}]
    assert len(evidence["charts"]) == 4
    for chart in evidence["charts"]:
        assert ET.parse(tmp_path / chart).getroot().tag.endswith("svg")
    svg = (tmp_path / "evidence/validation_history.svg").read_text()
    assert "x=3, y=2" in svg and "Actual optimizer updates" in svg
    with (tmp_path / "evidence/trials.csv").open(newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["test_RES_db"] == "" and row["optimizer_steps"] == "3"
    assert "test_comparison.svg" not in evidence["charts"]
    assert "|\n\n|" not in (tmp_path / "report.md").read_text()


def test_claimed_state_success_without_test_remains_unverified(tmp_path: Path) -> None:
    evidence = collect_evidence(tmp_path, _manifest(), {"status": "goal_met_within_budget", "final_comparison": {"passed": True}})
    assert evidence["acceptance"]["scientific_goal_met"] is None
    assert evidence["acceptance"]["engineering_complete"] is False


def test_data_diagnostics_and_source_lineage_are_linked(tmp_path: Path) -> None:
    diagnostic_path = tmp_path / "execution/baseline_preflight/attempt_01/data_diagnostics.json"
    summary = {"statistics_split": "train", "held_out_statistics_included": False, "training_samples": 123,
               "arrays": {"x": {"mean_power_scaled": 1.25, "mean_abs_off_diagonal_correlation": .5}}}
    atomic_json(diagnostic_path, {"summary": summary})
    atomic_json(tmp_path / "coding/round_01.receipt.json", {"source_commit": "candidate-sha", "parent_source_commit": "base-sha", "gate5": "passed"})
    (tmp_path / "coding/round_01.patch").write_text("layout-test-patch\n")
    trial = {"status": "completed", "operation": "preflight", "data_diagnostics":
             {"path": str(diagnostic_path), "sha256": "diagnostic-sha", "summary": summary}}
    manifest = {**_manifest(), "created_at": "2026-01-01T00:00:00+00:00"}
    state = {"status": "prepared_only", "completed_at": "2026-01-01T00:00:05+00:00", "trials": {"baseline_preflight": trial}}
    write_report(tmp_path, manifest, state)
    report = (tmp_path / "report.md").read_text()
    assert "1.25" in report and "diagnostic-sha" in report
    assert "[coding/round_01.patch](coding/round_01.patch)" in report
    assert "base-sha" in report and "candidate-sha" in report
    evidence = json.loads((tmp_path / "evidence/summary.json").read_text())
    assert evidence["resource_usage"]["run_wall_seconds"] == 5
    assert evidence["data_diagnostics"][0]["path"] == diagnostic_path.relative_to(tmp_path).as_posix()
