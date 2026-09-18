"""Validate actual native submission definitions without models or tool doubles."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from jsonschema import Draft202012Validator
import pytest

from app.agents.base import BaseAgent, RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.focused_agent import FocusedIdeaAgent
from app.agents.idea.parameter_schema import parameter_budget_schema
from app.agents.idea.research import parameter_errors
from app.agents.research_cli import (
    ResearchAnalysisAgent, ResearchCodingAgent, ResearchExperimentAgent, ResearchFinalReportAgent,
)
from app.harness.agent_loop.native_protocol import SUBMIT_DOCUMENT, native_specs
from app.settings import repo_root


def ledger() -> dict[str, Any]:
    # Human-authored arithmetic fixture, not a scientific proposal or model output.
    return {"unit": "real_scalar", "variables": {"N": 4, "R": 2},
            "baseline_formula": "2*N*N", "baseline_parameters": 32,
            "baseline_components": [{"name": "base", "formula": "2*N*N", "dtype": "complex", "shape": ["N", "N"]}],
            "candidate_formula": "2*N*R", "candidate_parameters": 16,
            "candidate_components": [{"name": "factor", "formula": "2*N*R", "dtype": "complex", "shape": ["N", "R"]}]}


def request(project: str = "folder_schema_contract") -> RunRequest:
    frozen = {"seed": 1, "max_steps": 1, "split_guard": 1, "train_fraction": .8,
              "validation_fraction": .1, "scale": 1, "fs": 1, "band": [-.5, .5],
              "metric": "fixture metric", "selection": "validation", "initialization": "from scratch"}
    return RunRequest(project=project, user_request="Human-authored schema contract input",
        upstream_artifacts={"frozen_protocol": json.dumps(frozen), "goal": '{"rounds":1}'},
        extra={"scope": "project_proposal", "idea_requirements": {"require_parameter_budget": True}})


def agent(profile: str) -> BaseAgent:
    if profile == "focused":
        return FocusedIdeaAgent()
    if profile == "legacy":
        return IdeaAgent()
    cls = {"coding": ResearchCodingAgent, "experiment": ResearchExperimentAgent,
           "analysis": ResearchAnalysisAgent, "final_report": ResearchFinalReportAgent}[profile]
    return cls("deepseek-v4-flash", {})


def native_parameters(subject: BaseAgent, req: RunRequest) -> dict[str, Any]:
    schema = subject.submission_schema(req)
    assert schema is not None
    specifications = native_specs([], schema)
    submission = next(spec["function"] for spec in specifications if spec["function"]["name"] == SUBMIT_DOCUMENT)
    parameters: dict[str, Any] = submission["parameters"]
    Draft202012Validator.check_schema(parameters)
    return parameters


def focused_document(project: str) -> dict[str, Any]:
    # Structure-only parser fixture; never sent to a provider or claimed as research.
    return {"metadata": {"schema": "proposal.v1", "project": project, "agent": "idea",
        "research_question": "Human-authored structure input", "hypothesis": "A structure-only hypothesis",
        "novelty": "No scientific novelty claimed", "human_summary": "Human-authored schema fixture",
        "method_spec": {"operation": "fixture"}, "decision_rule": {"metric": "fixture"},
        "handoff": {"version": "idea.handoff.v1", "target_agent": "experiment", "scope": "project_proposal",
            "next_step": "Validate the structure only",
            "changes": [{"target": "fixture", "operation": "add", "spec_ref": "/method_spec/operation", "preserve": []}],
            "verification_requirements": [{"id": "fixture", "question": "structure", "comparison": "fixture",
                "metric": "fixture", "decision_rule_ref": "/decision_rule"}], "required_context": []},
        "research_context": {"schema": "idea.research_context.v1", "question": "Structure only",
            "selection_principles": ["Schema fixture"], "sources": [{"source_id": "", "title": "Authored fixture",
                "url": "https://example.org/fixture", "decision": "defer", "reason": "No source was retrieved"}],
            "stop_reason": "This is not research", "open_questions": []},
        "parameter_budget": ledger()}, "body": "Human-authored schema fixture"}


@pytest.mark.parametrize("profile", ["focused", "legacy", "coding", "experiment", "analysis", "final_report"])
def test_project_identity_is_in_native_schema_and_is_request_local(profile: str) -> None:
    subject = agent(profile)
    path = repo_root() / "backend/app/harness/schema/schemas" / (subject.output_schema + ".json")
    original = path.read_bytes()
    first = native_parameters(subject, request("folder_first"))
    second = native_parameters(subject, request("folder_second"))
    first_project = first["properties"]["metadata"]["properties"]["project"]
    second_project = second["properties"]["metadata"]["properties"]["project"]
    assert first_project == {"type": "string", "minLength": 1, "const": "folder_first"}
    assert second_project["const"] == "folder_second"
    assert not list(Draft202012Validator(first_project).iter_errors("folder_first"))
    assert list(Draft202012Validator(first_project).iter_errors("pimc"))
    assert path.read_bytes() == original
    assert "const" not in json.loads(original)["properties"]["project"]


def test_wrong_project_is_rejected_by_complete_native_submission_definition() -> None:
    req = request()
    definition = native_parameters(FocusedIdeaAgent(), req)
    document = focused_document(req.project)
    validator = Draft202012Validator(definition)
    assert not list(validator.iter_errors(document))
    document["metadata"]["project"] = "pimc"
    errors = list(validator.iter_errors(document))
    assert len(errors) == 1
    assert list(errors[0].absolute_path) == ["metadata", "project"]
    assert errors[0].validator == "const"


@pytest.mark.parametrize("shape", ["N*R", "[N,R]", [1] * 9, [0], [-1], [True], [1.5]])
@pytest.mark.parametrize("prefix", ["baseline", "candidate"])
def test_wrong_shape_is_rejected_in_native_metadata_schema(shape: Any, prefix: str) -> None:
    req = request()
    definition = native_parameters(FocusedIdeaAgent(), req)
    document = focused_document(req.project)
    document["metadata"]["parameter_budget"][prefix + "_components"][0]["shape"] = shape
    errors = list(Draft202012Validator(definition).iter_errors(document))
    expected = ["metadata", "parameter_budget", prefix + "_components", 0, "shape"]
    assert errors and all(list(error.absolute_path)[:5] == expected for error in errors)


@pytest.mark.parametrize("field,value", [
    ("unit", "complex_elements"), ("variables", {"N": "4", "R": 2}),
    ("variables", {"N": True, "R": 2}), ("variables", {}),
    ("baseline_parameters", True), ("candidate_components", []), ("candidate_formula", "1" * 513),
])
def test_budget_definition_exposes_field_contracts(field: str, value: Any) -> None:
    payload = ledger()
    payload[field] = value
    assert list(Draft202012Validator(parameter_budget_schema()).iter_errors(payload))


@pytest.mark.parametrize("field", ["name", "formula", "dtype", "shape"])
def test_every_component_field_is_required(field: str) -> None:
    payload = ledger()
    del payload["candidate_components"][0][field]
    assert list(Draft202012Validator(parameter_budget_schema()).iter_errors(payload))


def test_scalar_array_is_valid_and_complex_count_remains_checked() -> None:
    payload = ledger()
    for prefix in ("baseline", "candidate"):
        payload[prefix + "_formula"] = "2"
        payload[prefix + "_parameters"] = 2
        payload[prefix + "_components"] = [{"name": "scalar", "formula": "2", "dtype": "complex", "shape": []}]
    assert not list(Draft202012Validator(parameter_budget_schema()).iter_errors(payload))
    assert parameter_errors(payload, max_ratio=1) == []
    payload["candidate_components"][0]["formula"] = "1"
    assert not list(Draft202012Validator(parameter_budget_schema()).iter_errors(payload))
    assert any("dtype/shape" in error for error in parameter_errors(payload, max_ratio=1))


def test_focused_and_legacy_share_budget_structure_without_expanding_focused_scope() -> None:
    focused = FocusedIdeaAgent().submission_schema(request())
    legacy = IdeaAgent().submission_schema(request())
    assert focused is not None and legacy is not None
    focused_budget = focused["properties"]["parameter_budget"]
    legacy_budget = legacy["properties"]["parameter_budget"]
    assert focused_budget["properties"] == legacy_budget["properties"]
    assert set(legacy_budget["required"]) - set(focused_budget["required"]) == {"evaluation_cases"}
    assert not {"evaluation_protocol", "signal_contract", "alternatives", "ablation_plan"}.intersection(focused["required"])
    assert {"evaluation_protocol", "signal_contract", "alternatives", "ablation_plan"} <= set(legacy["required"])
    assert not list(Draft202012Validator(focused_budget).iter_errors(ledger()))
    assert list(Draft202012Validator(legacy_budget).iter_errors(ledger()))
    payload = ledger()
    payload["evaluation_cases"] = [{"name": "primary", "variables": deepcopy(payload["variables"]),
                                   "baseline_parameters": 32, "candidate_parameters": 16}]
    assert not list(Draft202012Validator(focused_budget).iter_errors(payload))
    assert not list(Draft202012Validator(legacy_budget).iter_errors(payload))
    assert parameter_errors(payload, max_ratio=.8) == []
    payload["evaluation_cases"][0]["candidate_formula"] = "2*N*R"
    assert list(Draft202012Validator(focused_budget).iter_errors(payload))


def test_budget_fragments_and_separate_invocations_are_independent() -> None:
    first = parameter_budget_schema()
    first["properties"]["baseline_parameters"]["const"] = 32
    assert "const" not in first["properties"]["candidate_parameters"]
    first["properties"]["baseline_components"]["items"]["properties"]["shape"]["maxItems"] = 1
    assert first["properties"]["candidate_components"]["items"]["properties"]["shape"]["maxItems"] == 8
    assert "const" not in parameter_budget_schema()["properties"]["baseline_parameters"]


@pytest.mark.parametrize("issue", ["formula", "complex_count", "sum", "ratio"])
def test_structural_validation_does_not_replace_arithmetic_acceptance(issue: str) -> None:
    payload = ledger()
    if issue == "formula":
        payload["candidate_formula"] = "UNKNOWN"
    elif issue == "complex_count":
        payload["candidate_components"][0]["formula"] = "N*R"
    elif issue == "sum":
        payload["candidate_formula"] = "2*N*R+1"
        payload["candidate_parameters"] = 17
    else:
        payload["candidate_formula"] = payload["baseline_formula"]
        payload["candidate_parameters"] = payload["baseline_parameters"]
        payload["candidate_components"] = deepcopy(payload["baseline_components"])
    assert not list(Draft202012Validator(parameter_budget_schema()).iter_errors(payload))
    assert parameter_errors(payload, max_ratio=.8)
