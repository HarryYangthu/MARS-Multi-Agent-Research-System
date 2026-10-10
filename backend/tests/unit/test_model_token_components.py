"""Real durable budget arithmetic; authored usage is not a provider response."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import pytest

from app.harness.llm.accounting import ResourceBudgetError, RunModelBudget, charged_token_component
from app.harness.llm.provider_base import LLMConfig, Message


def policy(**limits: Any) -> dict[str, Any]:
    return {"schema": "runtime.resources.v1", "currency": "CNY", "prices": {}, "limits": {
        "max_model_requests": 10, "max_total_tokens": 100_000,
        "max_input_tokens": 50_000, "max_billed_output_tokens": 1000,
        "max_parallel_model_calls": 2, "max_elapsed_seconds": 60, "max_cost": None, **limits}}


def config() -> LLMConfig:
    return LLMConfig(provider="custom", model="budget-arithmetic", max_tokens=100, max_retries=0)


def messages() -> list[Message]:
    return [Message("user", "Temporary file accounting input. No model or tool is executed.")]


def read(budget: RunModelBudget) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(budget.path.read_text())
    return result


@pytest.mark.parametrize("key,value,error", [
    ("max_billed_output_tokens", 99, "output-token"),
    ("max_input_tokens", 10, "input-token"),
])
def test_components_reject_before_call_even_with_ample_combined_budget(
    tmp_path: Path, key: str, value: int, error: str,
) -> None:
    ledger = RunModelBudget(tmp_path, configuration=policy(**{key: value}), token_mode="limited")
    with pytest.raises(ResourceBudgetError, match=error):
        ledger.reserve(messages(), config(), {})
    assert not ledger.path.exists()


def test_each_retry_reserves_both_components_before_execution(tmp_path: Path) -> None:
    ledger = RunModelBudget(tmp_path, configuration=policy(max_billed_output_tokens=299), token_mode="limited")
    with pytest.raises(ResourceBudgetError, match="output-token"):
        ledger.reserve(messages(), replace(config(), max_retries=2), {})
    ledger = RunModelBudget(tmp_path, configuration=policy(max_billed_output_tokens=300), token_mode="limited")
    reservation = ledger.reserve(messages(), replace(config(), max_retries=2), {})
    row = read(ledger)["requests"][reservation.request_id]
    assert row["reserved_output_tokens"] == row["charged_output_tokens"] == 300
    assert row["reserved_input_tokens"] > 0
    assert row["reserved_tokens"] == row["reserved_input_tokens"] + row["reserved_output_tokens"]
    ledger.settle(reservation, usage=None, complete=False, outcome="failed")
    assert read(ledger)["requests"][reservation.request_id]["charged_output_tokens"] == 300
    with pytest.raises(ResourceBudgetError, match="output-token"):
        ledger.reserve(messages(), config(), {})


def test_settlement_uses_usage_and_charges_unexplained_remainder_as_output(tmp_path: Path) -> None:
    selected = policy()
    selected["prices"] = {"custom/budget-arithmetic": {"input_per_million": 1, "output_per_million": 5}}
    ledger = RunModelBudget(tmp_path, configuration=selected)
    reservation = ledger.reserve(messages(), config(), {})
    # Authored ledger settlement arithmetic: no successful service is simulated.
    ledger.settle(reservation, usage={"prompt_tokens": 40, "completion_tokens": 30, "total_tokens": 85},
                  complete=True, outcome="completed", sdk_attempts=1, attempts_complete=True)
    row = read(ledger)["requests"][reservation.request_id]
    assert row["charged_input_tokens"] == 40
    assert row["charged_output_tokens"] == 45
    assert row["charged_tokens"] == 85
    assert row["charged_cost"] == pytest.approx((40 + 45 * 5) / 1_000_000)
    assert not row["reservation_exceeded"]


@pytest.mark.parametrize("usage,complete", [
    (None, False),
    ({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, False),
    ({"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 1}, True),
    ({"prompt_tokens": True, "completion_tokens": 1, "total_tokens": 2}, True),
])
def test_unknown_or_invalid_usage_cannot_refund_either_component(
    tmp_path: Path, usage: Any, complete: bool,
) -> None:
    ledger = RunModelBudget(tmp_path, configuration=policy())
    reservation = ledger.reserve(messages(), config(), {})
    before = read(ledger)["requests"][reservation.request_id]
    ledger.settle(reservation, usage=usage, complete=complete, outcome="failed")
    after = read(ledger)["requests"][reservation.request_id]
    for part in ("input", "output"):
        assert after[f"charged_{part}_tokens"] == before[f"reserved_{part}_tokens"]
    assert not after["usage_complete"]


def test_output_limit_applies_to_all_requests_after_process_reopen(tmp_path: Path) -> None:
    selected = policy(max_billed_output_tokens=150)
    ledger = RunModelBudget(tmp_path, configuration=selected, token_mode="limited")
    reservation = ledger.reserve(messages(), config(), {})
    ledger.settle(reservation, usage={"prompt_tokens": 10, "completion_tokens": 80, "total_tokens": 90},
                  complete=True, outcome="completed")
    reopened = RunModelBudget(tmp_path, configuration=selected, token_mode="limited")
    with pytest.raises(ResourceBudgetError, match="output-token"):
        reopened.reserve(messages(), config(), {})
    smaller = reopened.reserve(messages(), replace(config(), max_tokens=70), {})
    reopened.settle(smaller, usage=None, complete=False, outcome="cancelled")


def test_legacy_records_are_conservatively_charged_in_each_unknown_bucket(tmp_path: Path) -> None:
    ledger = RunModelBudget(tmp_path, configuration=policy())
    reservation = ledger.reserve(messages(), config(), {})
    ledger.settle(reservation, usage=None, complete=False, outcome="failed")
    raw = read(ledger)
    row = raw["requests"][reservation.request_id]
    for key in ("reserved_input_tokens", "charged_input_tokens", "reserved_output_tokens", "charged_output_tokens"):
        row.pop(key)
    ledger.path.write_text(json.dumps(raw))
    assert charged_token_component(row, "input") == row["charged_tokens"]
    assert charged_token_component(row, "output") == row["charged_tokens"]
    # Reopen validates legacy bytes without resetting accumulated usage.
    assert RunModelBudget(tmp_path, configuration=policy()).recover_abandoned() == ()


@pytest.mark.parametrize("changes", [
    {"charged_input_tokens": -1}, {"charged_output_tokens": True},
    {"charged_output_tokens": 0}, {"charged_input_tokens": None},
])
def test_corrupt_split_records_fail_closed(tmp_path: Path, changes: dict[str, Any]) -> None:
    ledger = RunModelBudget(tmp_path, configuration=policy())
    reservation = ledger.reserve(messages(), config(), {})
    ledger.settle(reservation, usage=None, complete=False, outcome="failed")
    raw = read(ledger)
    raw["requests"][reservation.request_id].update(changes)
    ledger.path.write_text(json.dumps(raw))
    with pytest.raises(ResourceBudgetError, match="token-component"):
        ledger.reserve(messages(), config(), {})


@pytest.mark.parametrize("value", [None, True, 0, -1, 1.5])
@pytest.mark.parametrize("key", ["max_input_tokens", "max_billed_output_tokens"])
def test_configured_component_limits_must_be_finite_positive_integers(
    tmp_path: Path, key: str, value: Any,
) -> None:
    with pytest.raises(ValueError, match=key):
        RunModelBudget(tmp_path, configuration=policy(**{key: value}))


def test_export_preserves_component_charges_without_reporting_unknown_as_zero(tmp_path: Path) -> None:
    from app.bridge.cli_research_report import write_report

    ledger = RunModelBudget(tmp_path, configuration=policy())
    reservation = ledger.reserve(messages(), config(), {})
    ledger.settle(reservation, usage=None, complete=False, outcome="cancelled")
    write_report(tmp_path, {"project": "token-budget-test", "task": "Inspect arithmetic", "budget": {}}, {})
    evidence = json.loads((tmp_path / "evidence/summary.json").read_text())
    row = evidence["model_budgets"][0]["requests"][0]
    assert row["charged_output_tokens"] == row["reserved_output_tokens"] == 100
    assert row["charged_input_tokens"] == row["reserved_input_tokens"] > 0
    report = (tmp_path / "report.md").read_text()
    assert "计费输出：100" in report and "不作为实际 Token 消耗" in report
    raw = read(ledger)
    for key in ("charged_tokens", "charged_input_tokens", "charged_output_tokens"):
        raw["requests"][reservation.request_id].pop(key)
    ledger.path.write_text(json.dumps(raw))
    write_report(tmp_path, {"project": "token-budget-test", "task": "Inspect incomplete input", "budget": {}}, {})
    report = (tmp_path / "report.md").read_text()
    assert "配额已记账 Token（包含未知调用保留量）：—" in report
    assert "输入：—；计费输出：—" in report


def test_report_rejects_inconsistent_split_totals_like_the_runtime(tmp_path: Path) -> None:
    from app.bridge.cli_research_report import write_report

    ledger = RunModelBudget(tmp_path, configuration=policy())
    reservation = ledger.reserve(messages(), config(), {})
    ledger.settle(reservation, usage=None, complete=False, outcome="cancelled")
    raw = read(ledger)
    raw["requests"][reservation.request_id].update(charged_input_tokens=1, charged_output_tokens=2)
    ledger.path.write_text(json.dumps(raw))
    with pytest.raises(ResourceBudgetError, match="inconsistent token-component"):
        ledger.reserve(messages(), config(), {})
    write_report(tmp_path, {"project": "token-budget-test", "task": "Inspect corrupt totals", "budget": {}}, {})
    evidence = json.loads((tmp_path / "evidence/summary.json").read_text())
    assert any("Invalid token-component" in warning for warning in evidence["warnings"])
    row = evidence["model_budgets"][0]["requests"][0]
    assert all(row[key] is None for key in ("charged_tokens", "charged_input_tokens", "charged_output_tokens"))
    assert "输入：—；计费输出：—" in (tmp_path / "report.md").read_text()
