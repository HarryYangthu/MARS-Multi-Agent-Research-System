"""Pure arithmetic regressions for dimensions missed during real Idea review."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from app.agents.idea.research import parameter_errors


def ledger() -> dict[str, Any]:
    # An arithmetic input, not an Agent answer or an invented tool observation.
    values = {"name": "values", "formula": "K*K", "dtype": "real", "shape": ["K", "K"]}
    axes = {"name": "axis_logits", "formula": "2*(K-1)", "dtype": "real", "shape": [2, "K-1"]}
    return {"unit": "real_scalar", "variables": {"K": 12},
            "baseline_formula": "K*K", "candidate_formula": "K*K+2*(K-1)",
            "baseline_parameters": 144, "candidate_parameters": 166,
            "baseline_components": [values], "candidate_components": [deepcopy(values), axes],
            "evaluation_cases": [
                {"name": "primary", "variables": {"K": 12}, "baseline_parameters": 144, "candidate_parameters": 166},
                {"name": "larger", "variables": {"K": 16}, "baseline_parameters": 256, "candidate_parameters": 286}]}


def test_all_declared_dimensions_are_counted() -> None:
    raw = ledger()
    assert parameter_errors(raw, max_ratio=1.2) == []
    raw["evaluation_cases"].append({"name": "small", "variables": {"K": 8},
                                    "baseline_parameters": 64, "candidate_parameters": 78})
    errors = parameter_errors(raw, max_ratio=1.2)
    assert any("/evaluation_cases/2" in e and "exceeds" in e for e in errors)
    assert raw["variables"] == {"K": 12}


def test_secondary_counts_cannot_hide_oversized_tensors() -> None:
    raw = ledger()
    raw["evaluation_cases"][1]["candidate_parameters"] = 270
    assert any("/evaluation_cases/1/candidate" in e for e in parameter_errors(raw, max_ratio=1.2))


@pytest.mark.parametrize("value", [None, [], {}, [None], [{}], [False]])
def test_malformed_case_collection_is_rejected(value: Any) -> None:
    raw = ledger()
    raw["evaluation_cases"] = value
    assert parameter_errors(raw, max_ratio=1.2)


@pytest.mark.parametrize("variables", [{"J": 16}, {"K": 16, "limit": 2}, {"K": True}, {"K": float("nan")}, {"K": -1}])
def test_secondary_variables_must_be_complete_and_numeric(variables: dict[str, Any]) -> None:
    raw = ledger()
    raw["evaluation_cases"][1]["variables"] = variables
    assert parameter_errors(raw, max_ratio=1.2)


def test_primary_case_and_unique_names_required() -> None:
    raw = ledger()
    raw["evaluation_cases"] = raw["evaluation_cases"][1:]
    assert any("primary variables" in e for e in parameter_errors(raw, max_ratio=1.2))
    raw = ledger()
    raw["evaluation_cases"][1]["name"] = "primary"
    assert any("unique" in e for e in parameter_errors(raw, max_ratio=1.2))


def test_cases_cannot_override_formulas_or_limit() -> None:
    raw = ledger()
    raw["evaluation_cases"][1]["candidate_formula"] = "K*K"
    assert parameter_errors(raw, max_ratio=1.2)


def test_complex_tensors_count_twice_at_each_size() -> None:
    raw = ledger()
    for label in ("baseline", "candidate"):
        raw[label + "_components"][0]["dtype"] = "complex"
        raw[label + "_components"][0]["formula"] = "2*K*K"
    raw.update(baseline_formula="2*K*K", candidate_formula="2*K*K+2*(K-1)",
               baseline_parameters=288, candidate_parameters=310)
    raw["evaluation_cases"] = [
        {"name": "primary", "variables": {"K": 12}, "baseline_parameters": 288, "candidate_parameters": 310},
        {"name": "small", "variables": {"K": 8}, "baseline_parameters": 128, "candidate_parameters": 142}]
    assert parameter_errors(raw, max_ratio=1.2) == []


def test_historical_ledger_remains_readable() -> None:
    raw = ledger()
    del raw["evaluation_cases"]
    assert parameter_errors(raw, max_ratio=1.2) == []


@pytest.mark.parametrize("limit", [float("nan"), float("inf"), 0, -1])
def test_invalid_host_limit_cannot_disable_budget_check(limit: float) -> None:
    assert parameter_errors(ledger(), max_ratio=limit)


def test_recorded_requirements_cannot_disappear_during_audit() -> None:
    from app.agents.idea.acceptance import validation_record_delivery_errors
    metadata: dict[str, Any] = {"parameter_budget": ledger()}
    del metadata["parameter_budget"]["evaluation_cases"]
    record = {"delivery_contract_version": "idea.handoff.v1", "evaluation_protocol_required": True,
              "parameter_cases_required": True}
    errors = validation_record_delivery_errors(metadata, record)
    assert any("/evaluation_protocol: required" in error for error in errors)
    assert any("/parameter_budget/evaluation_cases: required" in error for error in errors)
    legacy_errors = validation_record_delivery_errors(metadata, {"delivery_contract_version": "idea.handoff.v1"})
    assert not any("/evaluation_protocol" in error or "/evaluation_cases" in error for error in legacy_errors)
