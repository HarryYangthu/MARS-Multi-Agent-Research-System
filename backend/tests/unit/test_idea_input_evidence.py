"""Caller-input archival and validation checks; no model responses are substituted."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.agents.base import RunRequest
from app.agents.idea.acceptance import (
    archive_baseline_input,
    model_input_contains_baseline,
    validation_record_delivery_errors,
    verify_baseline_input,
)
from app.agents.idea.agent import IdeaAgent
from app.harness.agent_loop.trace import digest
from app.harness.schema.frontmatter_parser import dumps


def test_caller_baseline_archive_preserves_exact_content_and_candidate_binding(tmp_path: Path) -> None:
    content = "# 调用方提供的公式检查输入\ndef baseline(x):\n    return x * x\n"
    receipt = archive_baseline_input(run_root=tmp_path, project="pimc", content=content,
                                     candidate_sha256=digest("human-authored candidate"))
    assert receipt is not None
    assert receipt["sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    restored, errors = verify_baseline_input(run_root=tmp_path, project="pimc",
                                           candidate_sha256=digest("human-authored candidate"), receipt=receipt)
    assert restored == content and errors == []
    assert receipt["bytes"] == len(content.encode("utf-8"))
    _, wrong_candidate = verify_baseline_input(run_root=tmp_path, project="pimc",
                                               candidate_sha256=digest("different candidate"), receipt=receipt)
    assert any("different candidate" in error for error in wrong_candidate)
    _, wrong_project = verify_baseline_input(run_root=tmp_path, project="other",
                                             candidate_sha256=digest("human-authored candidate"), receipt=receipt)
    assert wrong_project


def test_input_archival_is_content_addressed_across_candidate_revisions(tmp_path: Path) -> None:
    content = "def baseline(x):\n    return x\n"
    first = archive_baseline_input(run_root=tmp_path, project="pimc", content=content,
                                   candidate_sha256=digest("first authored version"))
    second = archive_baseline_input(run_root=tmp_path, project="pimc", content=content,
                                    candidate_sha256=digest("second authored version"))
    assert first is not None and second is not None
    assert first["path"] == second["path"]
    assert first["candidate_sha256"] != second["candidate_sha256"]
    for receipt in (first, second):
        restored, errors = verify_baseline_input(run_root=tmp_path, project="pimc",
                                               candidate_sha256=receipt["candidate_sha256"], receipt=receipt)
        assert restored == content and not errors


def test_changed_or_missing_archived_bytes_cannot_pass_input_verification(tmp_path: Path) -> None:
    content = "def baseline(x):\n    return x\n"
    receipt = archive_baseline_input(run_root=tmp_path, project="pimc", content=content,
                                     candidate_sha256=digest("authored candidate"))
    assert receipt is not None
    path = tmp_path / receipt["path"]
    document = json.loads(path.read_text(encoding="utf-8"))
    document["content"] = "def baseline(x):\n    return x + 1\n"
    path.write_text(json.dumps(document), encoding="utf-8")
    restored, errors = verify_baseline_input(run_root=tmp_path, project="pimc",
                                           candidate_sha256=receipt["candidate_sha256"], receipt=receipt)
    assert restored is None and any("hash mismatch" in error for error in errors)
    with pytest.raises(ValueError, match="existing baseline input archive"):
        archive_baseline_input(run_root=tmp_path, project="pimc", content=content,
                               candidate_sha256=digest("new authored candidate"))
    path.unlink()
    restored, errors = verify_baseline_input(run_root=tmp_path, project="pimc",
                                           candidate_sha256=receipt["candidate_sha256"], receipt=receipt)
    assert restored is None and errors


@pytest.mark.parametrize("patch", [
    {"path": "../another-run/input.json"},
    {"source": "model_claim"},
    {"kind": "citation"},
    {"bytes": True},
    {"bytes": 1},
    {"sha256": "not-a-sha256"},
])
def test_untrusted_receipt_metadata_is_revalidated(tmp_path: Path, patch: dict[str, Any]) -> None:
    receipt = archive_baseline_input(run_root=tmp_path, project="pimc", content="caller baseline input",
                                     candidate_sha256=digest("authored candidate"))
    assert receipt is not None
    restored, errors = verify_baseline_input(run_root=tmp_path, project="pimc",
                                           candidate_sha256=receipt["candidate_sha256"], receipt={**receipt, **patch})
    assert restored is None and errors


def test_empty_baseline_has_no_input_evidence(tmp_path: Path) -> None:
    assert archive_baseline_input(run_root=tmp_path, project="pimc", content=" \n\t",
                                 candidate_sha256=digest("authored candidate")) is None
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.asyncio
async def test_input_matching_uses_actual_context_messages_and_not_model_assertions() -> None:
    content = "def baseline(x):\n    return x * x\n"
    request = RunRequest(project="pimc", user_request="Study the supplied baseline",
                         upstream_artifacts={"baseline_code": content},
                         extra={"scope": "project_proposal", "context_sources": {
                             "project_rules": False, "code_repositories": False}})
    agent = IdeaAgent()
    context = await agent.build_context(request)
    messages = [message.to_wire() for message in agent._messages_for_context(request, context, purpose="input-contract")]
    assert model_input_contains_baseline(messages, content)
    assert not model_input_contains_baseline(messages, content + "# not supplied")
    assert not model_input_contains_baseline(
        [{"role": "assistant", "content": "[untrusted upstream:baseline_code]\n" + content}], content,
    )
    assert not model_input_contains_baseline(
        [{"role": "user", "content": "A document claims the baseline was read."}], content,
    )


@pytest.mark.asyncio
async def test_project_validation_records_caller_input_even_when_delivery_is_invalid(tmp_path: Path) -> None:
    # An incomplete human-authored document checks validation receipts, not Agent success.
    metadata: dict[str, Any] = {"schema": "proposal.v1", "project": "pimc", "agent": "idea",
                "research_question": "Which changes should be investigated?",
                "hypothesis": "A supplied baseline still needs research and validation.",
                "novelty": "No novelty is established.", "testable_predictions": ["A test is required."],
                "risk_register": ["Data has not been supplied."]}
    metadata.update({"human_summary": "比较基线和候选方法，实际收益需要后续实验验证。",
                     "related_literature": [], "method_spec": {"candidate": "An authored method definition"},
                     "decision_rule": {"definition": "An authored comparison definition"},
                     "handoff": {"version": "idea.handoff.v1", "target_agent": "experiment", "scope": "project_proposal",
                                 "next_step": "Prepare a comparison after checking the method reference.",
                                 "changes": [{"target": "component", "operation": "modify", "spec_ref": "/method_spec/missing", "preserve": []}],
                                 "verification_requirements": [{"id": "V1", "question": "Does it improve?", "comparison": "baseline vs candidate",
                                                                "metric": "error", "decision_rule_ref": "/decision_rule"}],
                                 "required_context": []}})
    text = dumps(metadata, "Human-authored validator input.")
    content = "def baseline(x):\n    return x * x\n"
    request = RunRequest(project="pimc", user_request="Review the supplied code",
                         upstream_artifacts={"baseline_code": content},
                         extra={"scope": "project_proposal", "run_root": str(tmp_path),
                                "idea_requirements": {"min_sources": 0, "min_pdfs": 0}})
    errors = await IdeaAgent().validate_candidate(request, text, [])
    assert errors and any("does not resolve" in error for error in errors)
    assert not any("/scope: project proposal requires" in error for error in errors)
    records = [json.loads(path.read_text()) for path in (tmp_path / "idea/validation").glob("*.json")]
    assert len(records) == 1
    record = records[0]
    assert record["delivery_contract_version"] == "idea.handoff.v1"
    assert record["candidate_sha256"] == digest(text)
    assert record["material_ready"] is False
    restored, input_errors = verify_baseline_input(run_root=tmp_path, project="pimc",
                                                 candidate_sha256=digest(text), receipt=record["input_evidence"][0])
    assert restored == content and input_errors == []
    assert not list(tmp_path.rglob("checkpoint.json"))


def test_delivery_audit_rechecks_new_contract_but_does_not_upgrade_legacy_receipts() -> None:
    assert validation_record_delivery_errors({}, {"scope": "project_proposal"}) == []
    errors = validation_record_delivery_errors({}, {
        "scope": "project_proposal", "delivery_contract_version": "idea.handoff.v1",
        "delivery_contract_valid": True,  # A stored verdict cannot replace rechecking the document.
    })
    assert any("/human_summary" in error for error in errors)
    assert any("/handoff" in error for error in errors)
    assert validation_record_delivery_errors({}, {"delivery_contract_version": "unknown"})


def test_delivery_audit_rejects_broken_method_reference() -> None:
    metadata = {"human_summary": "根据给定基线检查改动位置，具体收益仍需后续实验验证。",
                "method_spec": {"known": "an authored method definition"},
                "handoff": {"scope": "project_proposal", "changes": [{"spec_ref": "/method_spec/missing"}]}}
    errors = validation_record_delivery_errors(metadata, {
        "scope": "project_proposal", "delivery_contract_version": "idea.handoff.v1",
    })
    assert any("does not resolve" in error for error in errors)
