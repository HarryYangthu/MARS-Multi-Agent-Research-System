"""Exact ledger identity and immutable real request replay; no provider or tool substitutes."""
from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.agents.base import RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.research import material_errors, parameter_errors
from app.agents.idea.research_delegate import load_delegated_research
from app.harness.agent_loop.protocol import parse_action
from app.harness.agent_loop.trace import digest
from app.harness.schema.frontmatter_parser import parse


def _ledger() -> dict[str, Any]:
    # Human-authored arithmetic input, not a generated Agent proposal.
    components = [
        {"name": "values", "formula": "K*K", "dtype": "real", "shape": ["K", "K"]},
        {"name": "bias", "formula": "K", "dtype": "real", "shape": ["K"]},
    ]
    return {"unit": "real_scalar", "variables": {"K": 2},
            "baseline_formula": "K*K+K", "candidate_formula": "K*K+K",
            "baseline_parameters": 6, "candidate_parameters": 6,
            "baseline_components": deepcopy(components), "candidate_components": components,
            "evaluation_cases": [{"name": "primary", "variables": {"K": 2},
                "baseline_parameters": 6, "candidate_parameters": 6,
                "candidate_formula": "K*K+K", "candidate_components": deepcopy(components)}]}


def test_exact_duplicate_primary_ledger_is_accepted_without_mutation() -> None:
    raw = _ledger()
    original = deepcopy(raw)
    assert parameter_errors(raw, max_ratio=1) == []
    assert raw == original
    # Inherited primary ledgers keep their historical behavior.
    del raw["evaluation_cases"][0]["candidate_formula"]
    del raw["evaluation_cases"][0]["candidate_components"]
    assert parameter_errors(raw, max_ratio=1) == []


@pytest.mark.parametrize("change", ["formula", "component_name", "shape", "dtype", "component_order"])
def test_equal_totals_do_not_make_a_different_ledger_the_primary(change: str) -> None:
    raw = _ledger()
    case = raw["evaluation_cases"][0]
    if change == "formula":
        case["candidate_formula"] = "K**2+K"  # Numerically equal; no symbolic normalization.
    elif change == "component_name":
        case["candidate_components"][0]["name"] = "different_values"
    elif change == "shape":
        case["candidate_components"][0]["shape"] = [1, "K*K"]
    elif change == "dtype":
        case["candidate_components"][0].update(dtype="complex", shape=["K"])
    else:
        case["candidate_components"].reverse()
    original = deepcopy(raw)
    assert parameter_errors(raw, max_ratio=1) == [
        "/parameter_budget/evaluation_cases: primary variables must be included explicitly with the unchanged "
        "primary candidate formula/components (inherited or exactly repeated); a different candidate ledger "
        "cannot replace the primary configuration"]
    assert raw == original


@pytest.mark.parametrize("missing", ["candidate_formula", "candidate_components"])
def test_incomplete_duplicate_overrides_remain_invalid(missing: str) -> None:
    raw = _ledger()
    del raw["evaluation_cases"][0][missing]
    assert any("supply both" in error for error in parameter_errors(raw, max_ratio=1))


@pytest.mark.parametrize("variables", [{}, {"K": 2, "unused": 1}, {"K": 3}, {"K": True}])
def test_duplicate_ledger_requires_complete_primary_variables(variables: dict[str, Any]) -> None:
    raw = _ledger()
    raw["evaluation_cases"][0]["variables"] = variables
    assert parameter_errors(raw, max_ratio=1)


@pytest.mark.parametrize("field", ["baseline_parameters", "candidate_parameters"])
def test_duplicate_primary_cannot_hide_incorrect_counts(field: str) -> None:
    raw = _ledger()
    raw["evaluation_cases"][0][field] = 5
    assert any("integer count must match formula" in error for error in parameter_errors(raw, max_ratio=1))


def test_duplicate_primary_preserves_host_limit_and_tensor_validation() -> None:
    raw = _ledger()
    assert any("exceeds" in error for error in parameter_errors(raw, max_ratio=0.9))
    raw["candidate_components"][0]["shape"] = [3, 2]
    raw["evaluation_cases"][0]["candidate_components"] = deepcopy(raw["candidate_components"])
    assert any("dtype/shape" in error for error in parameter_errors(raw, max_ratio=1))


@pytest.mark.asyncio
async def test_author_and_schema_describe_exact_duplicate_primary_ledger() -> None:
    request = RunRequest("pimc", "Human-authored ledger contract", extra={
        "context_sources": {"project_rules": False, "code_repositories": False},
        "idea_requirements": {"require_parameter_budget": True}})
    agent = IdeaAgent()
    context = await agent.build_context(request)
    assert "either inherit or exactly repeat its candidate_formula and candidate_components" in context.task
    assert "Include the primary configuration without overrides" not in context.task
    schema = agent.submission_schema(request)
    assert schema is not None
    budget_schema = schema["properties"]["parameter_budget"]
    assert "inherit or exactly repeat" in budget_schema["properties"]["evaluation_cases"]["description"]
    assert not list(Draft202012Validator(budget_schema).iter_errors(_ledger()))


@pytest.fixture(scope="module")
def run18_request4() -> Iterator[dict[str, Any]]:
    configured = os.environ.get("MARS_TEST_IDEA_PRIMARY_CASE_EVENTS")
    if not configured:
        pytest.skip("requires actual run-18 lead events.jsonl; no replacement is generated")
    path = Path(configured)
    events: list[dict[str, Any]] = []
    prefix = b""
    with path.open("rb") as handle:
        for line in handle:
            event = json.loads(line)
            prefix += line
            events.append(event)
            if event["event_seq"] == 26:
                break
    assert len(events) == 26
    assert hashlib.sha256(prefix).hexdigest() == "de8b82499f3a225325b1545c3bb9cc9350a7938ea2d8452c517721af7c5d546f"
    observations = [event["visible"] for event in events if event["kind"] == "observation"]
    # The real research child is complete; hash only this child and its immutable receipts,
    # not the active lead checkpoint or events appended after the fixed prefix.
    root = path.parents[3]
    identifier = observations[0]["output"]["delegation_id"]
    child = root / "agent_traces/idea_research" / identifier
    child_state = json.loads((child / "checkpoint.json").read_text())
    assert child_state["status"] == "passed"
    paths = [file for file in child.rglob("*") if file.is_file()]
    delegation = root / "idea/research_delegations" / identifier
    paths += [file for file in delegation.rglob("*") if file.is_file()]
    for observation in child_state["history"]:
        output = observation.get("output")
        if not isinstance(output, dict):
            continue
        for source in output.get("sources", []):
            paths += [Path(source[key]) for key in ("read_receipt", "download_path") if source.get(key)]
    hashes = {file: hashlib.sha256(file.read_bytes()).hexdigest() for file in paths}
    yield {"root": root, "events": events, "observations": observations}
    with path.open("rb") as handle:
        assert handle.read(len(prefix)) == prefix
    assert all(hashlib.sha256(file.read_bytes()).hexdigest() == sha for file, sha in hashes.items())


def test_real_candidate_loses_only_the_duplicate_primary_error(run18_request4: dict[str, Any]) -> None:
    archive = run18_request4
    response = next(event for event in archive["events"]
                    if event["kind"] == "model_response" and event["request"] == 4)
    assert digest(response["visible"]) == response["visible_sha256"]
    text = parse_action(response["visible"])["final"]
    assert hashlib.sha256(text.encode()).hexdigest() == "97592ca0104d1e8fb66e23cd191d642fdc778ed8c6d76e4cad0fa64289c4da50"
    metadata = parse(text).metadata
    original = deepcopy(metadata)
    budget = metadata["parameter_budget"]
    primary = budget["evaluation_cases"][0]
    assert all(primary[field] == budget[field] for field in (
        "variables", "candidate_formula", "candidate_components", "baseline_parameters", "candidate_parameters"))
    assert parameter_errors(budget, max_ratio=1) == []
    reports, child_observations = load_delegated_research(archive["root"], archive["observations"])
    assert len(reports) == 1
    errors = material_errors(metadata, [*archive["observations"], *child_observations],
                             min_sources=2, min_pdfs=2, require_budget=True, max_ratio=1)
    assert errors == [
        "/evaluation_protocol/datasets: train and held-out datasets cannot share a canonical spec_ref",
        "/alternatives/2: component formula does not match dtype/shape count",
    ]
    recorded = next(event["visible"] for event in archive["events"] if event["event_seq"] == 26)
    assert recorded == [errors[0], "/parameter_budget/evaluation_cases: primary variables must be included explicitly", errors[1]]
    assert metadata == original
