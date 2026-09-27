"""Authored ledger-file arithmetic inputs, never provider or execution results."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.bridge.cli_research_report import collect_evidence, write_report
from app.harness.agent_loop.trace import atomic_json


def _manifest() -> dict[str, Any]:
    return {"project": "attempt-report-contract", "task": "Render accounting input", "budget": {}}


def _ledger(root: Path, requests: dict[str, dict[str, Any]]) -> Path:
    target = root / "resources/model_budget.v1.json"
    atomic_json(target, {"schema": "runtime.model_budget.v1", "configuration": {
        "limits": {"max_model_requests": 60}}, "requests": requests})
    return target


def test_report_distinguishes_logical_calls_observed_attempts_and_reserved_quota(tmp_path: Path) -> None:
    path = _ledger(tmp_path, {
        "settled": {"max_sdk_attempts": 3, "charged_attempts": 1, "observed_attempts": 1,
                    "attempts_complete": True, "status": "failed"},
        "retried": {"max_sdk_attempts": 2, "charged_attempts": 2, "observed_attempts": 2,
                    "attempts_complete": True, "status": "failed"},
        "cancelled": {"max_sdk_attempts": 4, "charged_attempts": 4, "observed_attempts": 1,
                      "attempts_complete": False, "status": "cancelled"},
        "legacy": {"max_sdk_attempts": 3, "status": "abandoned"},
    })
    original = path.read_bytes()
    write_report(tmp_path, _manifest(), {"status": "interrupted", "trials": {}})
    evidence = json.loads((tmp_path / "evidence/summary.json").read_text())
    ledger = evidence["model_budgets"][0]
    assert ledger["attempt_accounting"] == {
        "logical_calls": 4, "observed_sdk_attempts": 4, "observed_count_complete": False,
        "calls_with_unknown_attempt_count": 2, "charged_sdk_attempts": 10,
        "originally_reserved_sdk_attempts": 12, "retained_unknown_sdk_attempts": 7,
    }
    assert next(row for row in ledger["requests"] if row["request_id"] == "legacy")["attempt_accounting"] == "legacy_conservative"
    # Ledger rows do not fabricate trace events or get added to trace totals.
    assert evidence["resource_usage"]["model_requests"] == evidence["resource_usage"]["sdk_attempts"] == 0
    report = (tmp_path / "report.md").read_text()
    assert "已入账逻辑调用 4 条，不等于实际 API 尝试次数" in report
    assert "已观测 SDK 尝试：4（已知下界）" in report
    assert "配额已记账尝试：10" in report and "初始预留尝试合计：12" in report
    assert "其中未知调用保留尝试：7" in report
    assert path.read_bytes() == original


def test_legacy_attempt_counts_stay_unknown_with_conservative_shared_fallback(tmp_path: Path) -> None:
    _ledger(tmp_path, {"ceiling": {"max_sdk_attempts": 3}, "older": {"status": "failed"}})
    write_report(tmp_path, _manifest(), {"status": "interrupted", "trials": {}})
    evidence = json.loads((tmp_path / "evidence/summary.json").read_text())
    summary = evidence["model_budgets"][0]["attempt_accounting"]
    assert summary["logical_calls"] == 2
    assert summary["observed_sdk_attempts"] is None
    assert summary["charged_sdk_attempts"] == summary["originally_reserved_sdk_attempts"] == 7
    assert summary["retained_unknown_sdk_attempts"] == 7
    assert summary["calls_with_unknown_attempt_count"] == 2
    report = (tmp_path / "report.md").read_text()
    assert "已观测 SDK 尝试：未知" in report
    assert "已观测 SDK 尝试：0" not in report


def test_complete_attempt_observations_have_exact_total(tmp_path: Path) -> None:
    _ledger(tmp_path, {"first": {"max_sdk_attempts": 3, "charged_attempts": 1,
                                "observed_attempts": 1, "attempts_complete": True},
                       "second": {"max_sdk_attempts": 3, "charged_attempts": 2,
                                  "observed_attempts": 2, "attempts_complete": True}})
    summary = collect_evidence(tmp_path, _manifest(), {})["model_budgets"][0]["attempt_accounting"]
    assert summary["observed_sdk_attempts"] == summary["charged_sdk_attempts"] == 3
    assert summary["observed_count_complete"] is True
    assert summary["retained_unknown_sdk_attempts"] == summary["calls_with_unknown_attempt_count"] == 0
    assert summary["originally_reserved_sdk_attempts"] == 6


def test_invalid_attempt_ledger_does_not_render_a_zero_or_partial_charge_total(tmp_path: Path) -> None:
    _ledger(tmp_path, {"invalid": {"max_sdk_attempts": 3, "charged_attempts": 0},
                       "valid": {"max_sdk_attempts": 1, "charged_attempts": 1,
                                 "observed_attempts": 1, "attempts_complete": True}})
    evidence = collect_evidence(tmp_path, _manifest(), {})
    summary = evidence["model_budgets"][0]["attempt_accounting"]
    assert summary["charged_sdk_attempts"] is None
    assert summary["originally_reserved_sdk_attempts"] is None
    assert summary["retained_unknown_sdk_attempts"] is None
    assert summary["observed_sdk_attempts"] == 1 and summary["observed_count_complete"] is False
    assert any("Invalid model-attempt accounting" in warning for warning in evidence["warnings"])
