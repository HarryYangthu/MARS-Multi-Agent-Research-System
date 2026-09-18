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


def _trace_contract(directory: Path, *, tokens: int, start: str, failed: bool = False) -> None:
    rows: list[dict[str, Any]] = [
        {"kind": "model_request", "request": 1, "model": "arithmetic-contract", "time": start},
        {"kind": "sdk_attempt_started", "request": 1, "time": start},
        {"kind": "model_response", "request": 1, "time": start,
         "usage": {"prompt_tokens": tokens, "completion_tokens": 2, "total_tokens": tokens + 2}},
    ]
    if failed:
        rows += [{"kind": "model_request", "request": 2, "time": start},
                 {"kind": "sdk_attempt_started", "request": 2, "time": start},
                 {"kind": "sdk_attempt_failed", "request": 2, "time": start},
                 {"kind": "model_error", "time": start}]
    _lines(directory / "events.jsonl", rows)
    atomic_json(directory / "facts.json", {"status": "model_error" if failed else "passed", "usage_complete": not failed,
                "counts": {"model_requests": 2 if failed else 1, "model_responses": 1,
                           "sdk_attempts": 2 if failed else 1, "tool_dispatches": 0}})


def test_reused_traces_keep_original_time_and_separate_new_usage(tmp_path: Path) -> None:
    """Copy arithmetic trace inputs; never rerun or emulate a provider."""
    import shutil
    original = tmp_path / "original"
    source_stage = original / "stages/idea/proposal"
    _trace_contract(source_stage / "agent_traces/idea/previous", tokens=100, start="2026-01-01T00:00:00Z", failed=True)
    atomic_json(source_stage / "resources/model_budget.v1.json", {"configuration": {"limits": {}},
                "requests": {"previous": {"status": "failed", "charged_tokens": 900, "usage_complete": False}}})
    current = tmp_path / "current"
    copied_stage = current / "reused_research/stage"
    shutil.copytree(source_stage, copied_stage)
    _trace_contract(current / "stages/experiment/plan/agent_traces/experiment/current",
                    tokens=10, start="2026-01-02T00:00:00Z")
    atomic_json(current / "resources/model_budget.v1.json", {"configuration": {"limits": {}},
                "requests": {"current": {"status": "completed", "charged_tokens": 12, "usage_complete": True}}})
    receipt = {"schema": "research.reuse.v1", "source_run_root": str(original), "source_stage_root": str(source_stage),
               "copied_stage_root": "reused_research/stage", "reused_at": "2026-01-02T00:00:00Z",
               "source_artifact_sha256": "contract-artifact-hash"}
    atomic_json(current / "context/research_reuse.json", receipt)
    state = {"status": "designing_experiment", "trials": {}}
    write_report(current, _manifest(), state)
    evidence = json.loads((current / "evidence/summary.json").read_text())
    new = evidence["resource_usage_by_origin"]["current_run"]
    old = evidence["resource_usage_by_origin"]["inherited_research"]
    assert new["model_requests"] == new["model_responses"] == 1
    assert new["total_tokens"] == 12 and new["usage_complete"] is True
    assert old["model_requests"] == 2 and old["total_tokens"] == 102
    assert old["sdk_failures"] == 1 and old["usage_complete"] is False
    assert evidence["resource_usage"]["model_requests"] == 3
    assert evidence["resource_usage"]["total_tokens"] == 114
    assert evidence["resource_usage"]["usage_complete"] is False
    old_calls = [row for row in evidence["model_calls"] if row["origin"] == "inherited_research"]
    assert all(row["started_at"] == "2026-01-01T00:00:00Z" for row in old_calls)
    assert all(row["source_run_root"] == str(original) for row in old_calls)
    assert {row["origin"] for row in evidence["model_budgets"]} == {"current_run", "inherited_research"}
    report = (current / "report.md").read_text()
    assert "复制证据不会重新发送这些 API 请求" in report
    assert "本运行新发生" in report and "继承的历史研究" in report
    with (current / "evidence/model_calls.csv").open(newline="") as stream:
        exported = list(csv.DictReader(stream))
    assert sum(row["origin"] == "current_run" for row in exported) == 1
    assert sum(row["origin"] == "inherited_research" for row in exported) == 2
    assert (source_stage / "agent_traces/idea/previous/events.jsonl").read_bytes() == (copied_stage / "agent_traces/idea/previous/events.jsonl").read_bytes()


def test_invalid_reuse_path_never_imports_outside_trace(tmp_path: Path) -> None:
    source = tmp_path / "outside"
    _trace_contract(source / "agent_traces/idea/previous", tokens=100, start="2026-01-01T00:00:00Z")
    root = tmp_path / "run"
    atomic_json(root / "context/research_reuse.json", {"schema": "research.reuse.v1", "copied_stage_root": "../outside"})
    evidence = collect_evidence(root, _manifest(), {})
    assert evidence["resource_usage"]["model_requests"] == 0
    assert evidence["resource_usage_by_origin"]["current_run"]["model_requests"] == 0
    assert evidence["warnings"] and "no valid archived stage" in evidence["warnings"][0]


def test_unattributed_archived_budget_is_not_new_run_spend(tmp_path: Path) -> None:
    archive = tmp_path / "reused_research/stage/resources/model_budget.v1.json"
    atomic_json(archive, {"configuration": {"limits": {}}, "requests": {"old": {"charged_tokens": 300}}})
    evidence = collect_evidence(tmp_path, _manifest(), {})
    assert evidence["model_budgets"][0]["origin"] == "unclassified_inherited"
    assert evidence["model_budgets"][0]["source_run_root"] is None
    assert evidence["resource_usage_by_origin"]["current_run"]["total_tokens"] == 0
