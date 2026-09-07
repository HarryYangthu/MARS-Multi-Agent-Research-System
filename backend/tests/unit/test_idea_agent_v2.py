"""Idea contracts on real context, serializers and files; live success uses the API CLI."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.agents.base import RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.research import material_errors, parameter_errors, write_evidence
from app.harness.agent_loop.protocol import parse_action
from app.harness.llm.model_registry import get_agent_config
from app.harness.schema.frontmatter_parser import parse
from app.harness.schema.validator import validate_document
from app.harness.tools.registry import ToolContext, get_registry
from app.settings import repo_root
from app.storage.run_store import RunStore


@pytest.mark.asyncio
async def test_idea_context_loads_actual_project_rules_and_evaluation_scope() -> None:
    agent = IdeaAgent()
    request = RunRequest(project="pimc", user_request="Improve 2D LUT",
                         extra={"scope": "method_proposal", "idea_requirements": {"max_parameter_ratio": 1.2}})
    context = await agent.build_context(request)
    assert (repo_root() / "projects/pimc/AGENTS.md").read_text() in context.project
    assert "No real project repository" in context.task
    assert '"max_parameter_ratio": 1.2' in context.task
    messages = agent._messages_for_context(request, context, purpose="native-context-contract")
    assert "final.metadata" not in "\n".join(m.content for m in messages)
    assert "YAML frontmatter" in "\n".join(m.content for m in messages)
    assert "Memory" in context.system


@pytest.mark.asyncio
async def test_idea_without_provider_cannot_write_a_successful_research_pack(tmp_path: Path) -> None:
    config = replace(get_agent_config("idea"), model_provider="unconfigured")
    agent = IdeaAgent(agent_config=config)
    request = RunRequest(project="pimc", user_request="Use real research", extra={"run_root": str(tmp_path)})
    with pytest.raises(RuntimeError, match="not configured"):
        await agent.run_loop(request, await agent.build_context(request))
    assert not list(tmp_path.rglob("idea_proposal*.md"))
    assert not list(tmp_path.rglob("*.pdf"))


def test_empty_research_archive_is_recorded_as_empty(tmp_path: Path) -> None:
    path = write_evidence(tmp_path, [])
    evidence = json.loads(path.read_text())
    assert evidence["papers"] == evidence["downloads"] == evidence["reads"] == []
    assert all(count == 0 for count in evidence["counts"].values())
    assert json.loads((path.parent / "tool_results.v1.json").read_text()) == []


@pytest.mark.parametrize("metadata,expected", [
    ({}, "/related_literature"),
    ({"related_literature": [{"title": "Invented reference", "url": "https://arxiv.org/abs/0000.00000"}]}, "not matched"),
    ({"research_artifacts": {"pdf_downloads": ["does-not-exist.pdf"]}}, "/evidence"),
    ({"debate_summary": {"rounds": 2}}, "/debate_summary/rounds"),
    ({"testable_predictions": []}, "/testable_predictions"),
    ({"risk_register": []}, "/risk_register"),
    ({"parameter_budget": {}}, "/parameter_budget/unit"),
    ({"method_spec": {}}, "/method_spec"),
    ({"signal_contract": {}}, "/signal_contract"),
    ({"alternatives": [{}]}, "/alternatives"),
    ({"ablation_plan": [{}, {}]}, "/ablation_plan"),
])
def test_untrusted_authored_metadata_cannot_replace_missing_evidence(metadata: dict[str, object], expected: str) -> None:
    # Negative validator inputs, never a tool/model replacement or success sample.
    errors = material_errors(metadata, [], min_sources=2, min_pdfs=1, require_budget=True, max_ratio=1.2)
    assert any(expected in error for error in errors)


def test_final_serialization_does_not_invent_downstream_fields() -> None:
    # A human-authored minimal document is valid schema input, not an agent answer.
    metadata = {"schema": "proposal.v1", "project": "pimc", "agent": "idea",
                "research_question": "How should a LUT be compared?",
                "hypothesis": "The proposed change requires an experiment.",
                "novelty": "Novelty has not been established."}
    text = parse_action(json.dumps({"final": {"metadata": metadata, "body": "# Manually authored schema input"}}))["final"]
    assert validate_document(text, expected_schema="proposal.v1").valid
    assert parse(text).metadata == metadata
    errors = material_errors(metadata, [], min_sources=2, min_pdfs=1, require_budget=True, max_ratio=1.2)
    assert errors  # Schema alone cannot promote this document to material-ready.
    assert "downstream_requirements" not in parse(text).metadata


@pytest.mark.parametrize("candidate_count", [285, 284, 307])
def test_parameter_ledger_checks_exact_arithmetic(candidate_count: int) -> None:
    metadata = {"unit": "real_scalar", "variables": {"K": 16},
                "baseline_formula": "K*K", "candidate_formula": "K*K+2*(K-2)+1",
                "baseline_parameters": 256, "candidate_parameters": candidate_count,
                "baseline_components": [{"name": "values", "formula": "K*K", "dtype": "real", "shape": ["K", "K"]}],
                "candidate_components": [{"name": "values", "formula": "K*K", "dtype": "real", "shape": ["K", "K"]},
                                         {"name": "axis parameters", "formula": "2*(K-2)", "dtype": "real", "shape": [2, "K-2"]},
                                         {"name": "coefficient", "formula": "1", "dtype": "real", "shape": []}]}
    assert bool(parameter_errors(metadata, max_ratio=1.2)) == (candidate_count != 285)


@pytest.mark.asyncio
async def test_project_scope_requires_real_baseline_evidence(tmp_path: Path) -> None:
    # A manually authored schema-valid document cannot imply code was inspected.
    agent = IdeaAgent()
    metadata = {"schema": "proposal.v1", "project": "pimc", "agent": "idea",
                "research_question": "How should this baseline be extended?",
                "hypothesis": "An extension still requires code evidence.",
                "novelty": "Novelty is unestablished.", "testable_predictions": ["Measure after integration"],
                "risk_register": ["Missing baseline code"]}
    text = parse_action(json.dumps({"final": {"metadata": metadata, "body": "# Manually supplied proposal"}}))["final"]
    errors = await agent.validate_candidate(
        RunRequest(project="pimc", user_request="Project-specific proposal",
                   extra={"scope": "project_proposal", "run_root": str(tmp_path),
                          "idea_requirements": {"min_sources": 0, "min_pdfs": 0}}), text, [])
    assert any("/scope: project proposal requires actual baseline code evidence" in error for error in errors)
    assert not list(tmp_path.rglob("*.approved.md"))


@pytest.mark.asyncio
async def test_malformed_download_arguments_are_rejected_by_actual_registry(tmp_path: Path) -> None:
    result = await get_registry().dispatch("search.fetch_sources", {"sources": "not-a-list"},
                                           ToolContext("invalid-download", "pimc", "idea",
                                                       extra={"run_root": str(tmp_path)}))
    assert result.ok is False
    assert result.error
    assert not list(tmp_path.rglob("*.pdf"))


def test_incomplete_run_acceptance_never_passes(tmp_path: Path) -> None:
    from app.agents.idea.acceptance import build_idea_acceptance_report
    run = RunStore(tmp_path).create(task="incomplete-real-run", project="pimc", entrypoint="idea",
                                   user_request="No successful agent run yet")
    report = build_idea_acceptance_report(run=run)
    assert "Overall: **PASS**" not in report
    assert "No proposal artifact found" in report
