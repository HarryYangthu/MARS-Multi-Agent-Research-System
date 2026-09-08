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


def test_candidate_override_requires_a_complete_formula_and_tensor_ledger() -> None:
    raw = ledger()
    raw["evaluation_cases"][1]["candidate_formula"] = "K*K"
    assert parameter_errors(raw, max_ratio=1.2)


def test_different_architectures_have_complete_independent_candidate_ledgers() -> None:
    raw = ledger()
    raw["evaluation_cases"] += [
        {"name": "frozen_positions", "variables": {"K": 12}, "baseline_parameters": 144,
         "candidate_parameters": 144, "candidate_formula": "K*K",
         "candidate_components": [deepcopy(raw["baseline_components"][0])]},
        {"name": "low_rank", "variables": {"K": 12, "R": 5}, "baseline_parameters": 144,
         "candidate_parameters": 120, "candidate_formula": "2*K*R", "candidate_components": [
             {"name": name, "formula": "K*R", "dtype": "real", "shape": ["K", "R"]}
             for name in ("U", "V")]},
    ]
    assert parameter_errors(raw, max_ratio=1.2) == []
    assert raw["candidate_formula"] == "K*K+2*(K-1)"
    assert raw["candidate_parameters"] == 166
    raw["evaluation_cases"][-1]["candidate_components"][1]["shape"] = ["K", "R+1"]
    assert any("dtype/shape" in e for e in parameter_errors(raw, max_ratio=1.2))


def test_candidate_override_cannot_evade_limit_or_omit_primary_configuration() -> None:
    raw = ledger()
    case = {"name": "oversized", "variables": {"K": 12}, "baseline_parameters": 144,
            "candidate_parameters": 288, "candidate_formula": "2*K*K", "candidate_components": [
                {"name": "complex_values", "formula": "2*K*K", "dtype": "complex", "shape": ["K", "K"]}]}
    raw["evaluation_cases"].append(case)
    assert any("/evaluation_cases/2" in e and "exceeds" in e for e in parameter_errors(raw, max_ratio=1.2))
    raw["evaluation_cases"] = [case]
    assert any("primary variables" in e for e in parameter_errors(raw, max_ratio=3))
    case["baseline_formula"] = "2*K*K"
    assert parameter_errors(raw, max_ratio=3)


@pytest.mark.parametrize("missing", ["candidate_formula", "candidate_components"])
def test_schema_and_host_both_require_complete_overrides(missing: str) -> None:
    from jsonschema import Draft202012Validator
    from app.agents.base import RunRequest
    from app.agents.idea.agent import IdeaAgent

    schema = IdeaAgent().submission_schema(RunRequest(project="pimc", user_request="arithmetic contract",
        extra={"idea_requirements": {"require_parameter_budget": True}}))
    assert schema is not None
    raw = ledger()
    raw["evaluation_cases"][1].update(candidate_formula="K*K", candidate_parameters=256,
                                       candidate_components=deepcopy(raw["baseline_components"]))
    budget_schema = schema["properties"]["parameter_budget"]
    assert not list(Draft202012Validator(budget_schema).iter_errors(raw))
    assert parameter_errors(raw, max_ratio=1.2) == []
    del raw["evaluation_cases"][1][missing]
    assert list(Draft202012Validator(budget_schema).iter_errors(raw))
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
