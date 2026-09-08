"""Real files, legacy carriage and explicit refusals; no fabricated successful execution."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

import pytest

from app.agents.idea.agent import IdeaAgent
from app.agents.idea.research_handoff import load_research_handoff
from app.bridge.agent_registry import AgentRegistry
from app.bridge.agent_runner import load_agent_handoff_context
from app.harness.schema.frontmatter_parser import dumps
from app.storage.run_store import RunStore


def declaration() -> dict[str, Any]:
    """A human-authored proposed contract, with no real evidence attached."""
    return {"schema": "proposal.v1", "project": "pimc", "agent": "idea",
            "research_question": "What could satisfy the specified parameter budget?",
            "hypothesis": "A human-authored hypothesis requiring evidence and actual experiments.",
            "novelty": "Not established by this declaration.",
            "human_summary": "这是人工编写的输入声明，没有附带真实研究证据。",
            "method_spec": {"candidate": "Human-authored method definition."},
            "related_literature": [{"title": "Human-authored reference", "url": "https://example.org/source"}],
            "research_links": [{"delegation_id": "declared-reading", "insight_id": "finding-a",
                                "method_spec_ref": "/method_spec/candidate",
                                "adaptation_reason": "A declared transfer that has not been checked against real research."}],
            "research_assessment": {
                "version": "idea.research_assessment.v1",
                "task_question": "Which design might satisfy the specified parameter budget?",
                "selection_principles": [{"id": "budget", "criterion": "Explains the number of trainable scalars",
                                          "task_basis": "The task declares a trainable-parameter constraint."}],
                "stopping_reason": "This is a human-authored stopping statement, not evidence of research execution.",
                "remaining_gaps": ["Real literature evidence and experiments are missing."],
                "source_decisions": [{"delegation_id": "declared-reading", "source_id": "source-a",
                    "decision": "adopt", "reason": "A human-authored source adoption declaration without evidence.",
                    "task_relevance": "The declaration is about the task's specified parameter budget.",
                    "criterion_ids": ["budget"], "insight_ids": ["finding-a"],
                    "transfer_assumptions": ["The declared counting convention applies to the target task."]}],
            }}


def proposal(metadata: dict[str, Any]) -> str:
    return dumps(metadata, metadata["human_summary"])


def test_legacy_proposal_needs_no_research_evidence_or_registry(tmp_path: Path) -> None:
    metadata = declaration()
    del metadata["research_assessment"]
    text = proposal(metadata)
    assert load_research_handoff(tmp_path, text, project="pimc") is None
    run = RunStore(tmp_path / "runs").create(task="legacy carriage", project="pimc", entrypoint="idea")
    (run.subdir("idea") / "idea_proposal.approved.md").write_text(text)
    upstream, _ = load_agent_handoff_context(run, "experiment", registry=AgentRegistry())
    assert upstream["idea_proposal.approved.md"].endswith(text)
    assert not any("research_evidence" in key for key in upstream)


def test_new_authored_proposal_without_recorded_execution_fails(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing recorded delegate receipts"):
        load_research_handoff(tmp_path, proposal(declaration()), project="pimc")


@pytest.mark.parametrize("assessment", [None, {}, [], {"version": "unknown"}])
def test_present_but_invalid_assessment_is_not_legacy(tmp_path: Path, assessment: Any) -> None:
    metadata = declaration()
    metadata["research_assessment"] = assessment
    with pytest.raises(ValueError, match="assessment"):
        load_research_handoff(tmp_path, proposal(metadata), project="pimc")


def test_proposal_cannot_relabel_the_run_project(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="project differs"):
        load_research_handoff(tmp_path, proposal(declaration()), project="another-project")


def test_authored_report_fields_and_arbitrary_files_do_not_replace_receipts(tmp_path: Path) -> None:
    metadata = declaration()
    metadata["reports"] = [{"report": "Human-authored text, not a trusted report."}]
    unrelated = tmp_path / "unrelated" / "report.json"
    unrelated.parent.mkdir()
    unrelated.write_text(json.dumps(metadata["reports"]))
    metadata["report_path"] = str(unrelated)
    with pytest.raises(ValueError, match="missing recorded delegate receipts"):
        load_research_handoff(tmp_path, proposal(metadata), project="pimc")


@pytest.mark.parametrize("identifier", ["../../outside", "/etc/passwd"])
def test_declared_delegation_id_is_not_used_as_a_file_path(tmp_path: Path, identifier: str) -> None:
    metadata = declaration()
    metadata["research_assessment"]["source_decisions"][0]["delegation_id"] = identifier
    metadata["research_links"][0]["delegation_id"] = identifier
    with pytest.raises(ValueError, match="missing recorded delegate receipts"):
        load_research_handoff(tmp_path, proposal(metadata), project="pimc")


@pytest.mark.parametrize("contents", ["not-json", "[]", "{}", '{"history":[42]}'])
def test_corrupt_checkpoint_fails_explicitly(tmp_path: Path, contents: str) -> None:
    checkpoint = tmp_path / "agent_traces/idea/authored-invalid-input/checkpoint.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(contents)
    with pytest.raises(ValueError, match="evidence verification failed"):
        load_research_handoff(tmp_path, proposal(declaration()), project="pimc")


def test_checkpoint_symlink_cannot_escape_the_run(tmp_path: Path) -> None:
    root = tmp_path / "run"
    checkpoint = root / "agent_traces/idea/invalid-link/checkpoint.json"
    checkpoint.parent.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("This file is outside the run and is not a checkpoint.")
    checkpoint.symlink_to(outside)
    with pytest.raises(ValueError, match="outside its run namespace"):
        load_research_handoff(root, proposal(declaration()), project="pimc")


def test_real_archived_checkpoint_alone_is_not_complete_evidence(tmp_path: Path) -> None:
    # Published 2026-09-08 attempt_04 is an original, unchanged subset archive.
    # It omits the parent event stream and PDF receipts, so it must not pass.
    archive = Path(__file__).resolve().parents[3] / "docs/evaluation/idea_research_20260908/attempt_04"
    relative = Path("agent_traces/idea/251dcd370ed44b74ad21151d8eb390b7/checkpoint.json")
    source = archive / relative
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(target.read_bytes()).digest()
    history = json.loads(source.read_text())["history"]
    recorded = next(row for row in history if row.get("tool") == "idea.research_delegate" and row.get("ok"))
    metadata = declaration()
    delegation_id = recorded["output"]["delegation_id"]
    metadata["research_assessment"]["source_decisions"][0]["delegation_id"] = delegation_id
    metadata["research_links"][0]["delegation_id"] = delegation_id
    with pytest.raises(ValueError, match="evidence file is missing"):
        load_research_handoff(tmp_path, proposal(metadata), project="pimc")


def test_bridge_requires_registered_evidence_loader_for_new_proposals(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="new handoff", project="pimc", entrypoint="idea")
    (run.subdir("idea") / "idea_proposal.approved.md").write_text(proposal(declaration()))
    with pytest.raises(ValueError, match="registered Idea agent"):
        load_agent_handoff_context(run, "experiment", registry=AgentRegistry())


def test_bridge_calls_real_idea_loader_and_propagates_missing_evidence(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="new handoff", project="pimc", entrypoint="idea")
    (run.subdir("idea") / "idea_proposal.approved.md").write_text(proposal(declaration()))
    registry = AgentRegistry()
    registry.register("idea", IdeaAgent())
    with pytest.raises(ValueError, match="missing recorded delegate receipts"):
        load_agent_handoff_context(run, "experiment", registry=registry)
    assert not (run.root / "agent_traces").exists()
