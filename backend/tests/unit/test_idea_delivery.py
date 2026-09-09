"""Contract and real-file tests; authored documents are parser inputs, not Agent results."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.agents.base import Artifact, RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.delivery import delivery_errors, progress_message, progress_sink, resolve_pointer, write_delivery
from app.harness.schema.frontmatter_parser import dumps
from app.harness.schema.validator import validate_metadata


def authored_metadata() -> dict[str, Any]:
    return {"schema": "proposal.v1", "project": "pimc", "agent": "idea",
            "research_question": "How to compare two methods?", "hypothesis": "A method requires actual validation.",
            "novelty": "Novelty is not established.", "human_summary": "比较两个方法，确认哪个值得进入后续实验。",
            "method_spec": {"candidate": {"definition": "human-authored parser input"}},
            "decision_rule": {"comparison": "specified by the user"},
            "handoff": {"version": "idea.handoff.v1", "target_agent": "experiment", "scope": "method_proposal",
                        "next_step": "Prepare the specified comparison after inputs are supplied.",
                        "changes": [{"target": "symbolic component", "operation": "modify", "spec_ref": "/method_spec/candidate", "preserve": []}],
                        "verification_requirements": [{"id": "V1", "question": "Which method?", "comparison": "A vs B", "metric": "error", "decision_rule_ref": "/decision_rule"}],
                        "required_context": [{"kind": kind, "description": "caller input", "reason": "required for execution", "blocks_execution": True}
                                             for kind in ("baseline_code", "data_description")]}}


def test_delivery_references_resolve_and_required_context_is_explicit() -> None:
    metadata = authored_metadata()
    assert validate_metadata(metadata).valid
    assert delivery_errors(metadata, "method_proposal") == []
    metadata["handoff"]["changes"][0]["spec_ref"] = "/method_spec/missing"
    assert any("does not resolve" in error for error in delivery_errors(metadata, "method_proposal"))
    metadata["handoff"]["required_context"] = []
    assert any("prerequisites" in error for error in delivery_errors(metadata, "method_proposal"))


def test_schema_rejects_ambiguous_or_excess_handoff_fields() -> None:
    metadata = authored_metadata()
    metadata["handoff"]["approved"] = True
    assert not validate_metadata(metadata).valid
    del metadata["handoff"]["approved"]
    metadata["handoff"]["verification_requirements"][0]["decision_rule_ref"] = "/other_rule"
    assert not validate_metadata(metadata).valid


def test_human_summary_is_bounded_without_losing_numeric_decimals() -> None:
    metadata = authored_metadata()
    metadata["human_summary"] = "候选参数控制在基线的1.2倍内。实际收益仍需验证。"
    assert not delivery_errors(metadata, "method_proposal")
    metadata["human_summary"] = "第一句话讲方案。第二句话讲目标。第三句话不应放在概览中。"
    assert any("two sentences" in error for error in delivery_errors(metadata, "method_proposal"))


def test_json_pointer_handles_escaped_keys_and_array_indices() -> None:
    assert resolve_pointer({"a/b": [{"~key": 4}]}, "/a~1b/0/~0key") == 4
    with pytest.raises(ValueError):
        resolve_pointer({"method_spec": {}}, "/method_spec/missing")


@pytest.mark.parametrize("pointer", ["/items/-1", "/items/01", "/items/+1", "/items/١", "/bad~2key"])
def test_handoff_pointers_reject_python_specific_indices_and_invalid_escapes(pointer: str) -> None:
    with pytest.raises(ValueError):
        resolve_pointer({"items": ["first", "second"], "bad~2key": "value"}, pointer)


@pytest.mark.asyncio
async def test_progress_persists_public_message_not_raw_candidate(tmp_path: Path) -> None:
    request = RunRequest("pimc", "task", extra={"run_root": str(tmp_path), "run_id": "run-1"})
    emit = progress_sink(request, "invocation-1")
    await emit({"kind": "action", "tool": "search.fetch_sources", "args": {"internal": "not-for-progress"}})
    await emit({"kind": "validation", "valid": False, "issues": ["issue-one", "issue-two"]})
    rows = [json.loads(line) for line in (tmp_path / "idea/progress.jsonl").read_text().splitlines()]
    assert rows[0]["message"] == "正在获取资料并读取指定页段。"
    assert "2 项问题" in rows[1]["message"]
    assert "not-for-progress" not in json.dumps(rows)


@pytest.mark.asyncio
async def test_reviewer_has_separate_instructions_and_supplied_context() -> None:
    agent = IdeaAgent()
    request = RunRequest("pimc", "Research task", upstream_artifacts={"background": "user supplied facts"},
                         extra={"context_sources": {"project_rules": False, "code_repositories": False}})
    context = await agent.build_context(request)
    messages = agent.review_messages(request, context)
    text = "\n".join(m.content for m in messages)
    assert "critical scientific methods reviewer" in text
    assert "user supplied facts" in text
    assert "Return the complete Markdown" not in text
    assert "Idea acceptance scope: method_proposal" in text
    assert "Missing measured improvement" in text
    assert "Judge this document independently" in text
    # The independent reviewer must receive the actual candidate contract,
    # not only the caller's shorter question with the host requirements omitted.
    assert context.task in text


def test_candidate_progress_never_claims_acceptance() -> None:
    message = progress_message({"kind": "candidate", "text": "---\nhuman_summary: 这是待验收的研究方案。\n---\n"})
    assert message.startswith("候选方案，尚待验收：")


def test_english_tool_explanations_have_a_chinese_factual_fallback() -> None:
    assert progress_message({"kind": "action", "tool": "search.arxiv_search", "reason": "Search more papers"}) == "正在检索相关论文。"


@pytest.mark.parametrize("accepted,expected", [
    (True, "当前审查项通过模型检查。"),
    (False, "当前审查项有待解决问题。"),
    (None, "当前审查项尚未确认结果。"),
    ("false", "当前审查项尚未确认结果。"),
])
@pytest.mark.parametrize("phase", ["act", "reflect"])
def test_review_unit_progress_describes_only_the_current_item(accepted: object, expected: str, phase: str) -> None:
    # Authored rendering inputs; no review or provider execution is represented.
    # A rejected item may remain in reflect while other items are collected.
    event = {"kind": "review_unit", "unit_id": "item", "accepted": accepted, "phase": phase}
    message = progress_message(event)
    assert message == expected
    assert all(word not in message for word in ("已停止", "已完成", "开始修订", "整份报告通过"))


def test_real_run19_review_unit_progress_no_longer_reports_stopped() -> None:
    configured = os.environ.get("MARS_TEST_IDEA_REVIEW_UNIT_PROGRESS")
    if not configured:
        pytest.skip("requires actual run-19 child progress; no replacement is generated")
    path = Path(configured)
    with path.open("rb") as stream:
        prefix = b"".join(stream.readline() for _ in range(21))
    assert hashlib.sha256(prefix).hexdigest() == "d6ea18dc8e9689706245d545b831a0b39cec7aef8b353e91b47a7cc39b0ac382"
    rows = [json.loads(line) for line in prefix.splitlines()]
    units = [row for row in rows if row["kind"] == "review_unit"]
    assert [row["accepted"] for row in units] == [False, True, False]
    assert all("本次运行已停止" in row["message"] for row in units)
    assert [progress_message(row) for row in units] == [
        "当前审查项有待解决问题。", "当前审查项通过模型检查。", "当前审查项有待解决问题。",
    ]
    with path.open("rb") as stream:
        assert stream.read(len(prefix)) == prefix


def test_repeated_delivery_preserves_exact_prior_artifact(tmp_path: Path) -> None:
    metadata = authored_metadata()
    original = dumps(metadata, metadata["human_summary"])
    request = RunRequest("pimc", "task", extra={"run_root": str(tmp_path), "scope": "method_proposal"})
    first = write_delivery(Artifact(original, "proposal.v1", metadata, ""), request,
                           invocation="same-invocation", reviewed=False)
    metadata["human_summary"] = "比较两个修订后的方法，确认哪个值得进入后续实验。"
    revised = dumps(metadata, metadata["human_summary"])
    second = write_delivery(Artifact(revised, "proposal.v1", metadata, ""), request,
                            invocation="same-invocation", reviewed=False)
    assert first != second
    assert (first / "proposal.md").read_text() == original
    assert (second / "proposal.md").read_text() == revised
    assert json.loads((second / "proposal.json").read_text())["human_summary"] == metadata["human_summary"]
    acceptance = json.loads((second / "acceptance.json").read_text())
    assert not acceptance["simulation_executed"]
    assert not acceptance["scientific_validated"]


def test_current_body_contract_prevents_duplicate_method_definitions() -> None:
    from app.agents.idea.acceptance import validation_record_delivery_errors
    metadata = authored_metadata()
    assert not delivery_errors(metadata, "method_proposal", body=metadata["human_summary"])
    record = {"delivery_contract_version": "idea.handoff.v1", "body_policy": "summary_only"}
    assert any("/body" in issue for issue in validation_record_delivery_errors(metadata, record, body="A second definition."))
    record.pop("body_policy")
    assert not validation_record_delivery_errors(metadata, record, body="Legacy narrative body.")


def test_submission_schema_requires_full_method_without_changing_legacy_parser() -> None:
    request = RunRequest("pimc", "task", extra={"idea_requirements": {"require_parameter_budget": True}})
    schema = IdeaAgent().submission_schema(request)
    assert schema is not None
    assert {"human_summary", "handoff", "method_spec", "parameter_budget", "ablation_plan"} <= set(schema["required"])
    assert {"research_assessment", "research_links"} <= set(schema["required"])
    assert schema["properties"]["alternatives"]["minItems"] == 2
    budget = schema["properties"]["parameter_budget"]
    assert budget["properties"]["variables"]["additionalProperties"] == {"type": "number"}
    assert "baseline_parameters" in budget["required"]
    assert budget["properties"]["baseline_components"]["items"]["properties"]["shape"]["type"] == "array"
    legacy = {"schema": "proposal.v1", "project": "pimc", "agent": "idea", "research_question": "Authored input?",
              "hypothesis": "Hypothesis input.", "novelty": "Novelty unknown."}
    assert validate_metadata(legacy).valid


@pytest.mark.asyncio
async def test_host_checks_submission_schema_even_when_provider_ignores_it() -> None:
    metadata = authored_metadata()
    del metadata["method_spec"]
    errors = await IdeaAgent().validate_candidate(RunRequest("pimc", "task"), dumps(metadata, "Parser input."), [])
    assert any("'method_spec' is a required property" in error for error in errors)


def test_artifact_conversion_preserves_exact_candidate_digest() -> None:
    from app.harness.llm.provider_base import Completion
    text = dumps(authored_metadata(), "Parser input.") + "\n\n"
    artifact = IdeaAgent()._artifact_from_completion(Completion(text, "parser", "parser"))
    assert artifact.text == text


@pytest.mark.parametrize("reviewed", [False, True])
def test_research_delivery_requires_actual_evidence_even_with_claimed_review(tmp_path: Path, reviewed: bool) -> None:
    metadata = authored_metadata()
    metadata["research_assessment"] = {"version": "idea.research_assessment.v1"}
    text = dumps(metadata, metadata["human_summary"])
    request = RunRequest("pimc", "task", extra={"run_root": str(tmp_path)})
    with pytest.raises(ValueError, match="research decisions"):
        write_delivery(Artifact(text, "proposal.v1", metadata, ""), request,
                       invocation="authored-negative-input", reviewed=reviewed)
    assert not (tmp_path / "idea" / "deliveries").exists()


def test_required_research_contract_cannot_be_dropped_at_export(tmp_path: Path) -> None:
    metadata = authored_metadata()
    text = dumps(metadata, metadata["human_summary"])
    request = RunRequest("pimc", "task", extra={"run_root": str(tmp_path),
                         "idea_requirements": {"require_research_dossier": True}})
    with pytest.raises(ValueError, match="research_assessment"):
        write_delivery(Artifact(text, "proposal.v1", metadata, ""), request,
                       invocation="authored-negative-input", reviewed=True)
    assert not (tmp_path / "idea" / "deliveries").exists()


@pytest.mark.asyncio
async def test_research_without_review_configuration_fails_before_model_call(tmp_path: Path) -> None:
    from dataclasses import replace
    from app.harness.llm.model_registry import get_agent_config
    config = get_agent_config("idea")
    config = replace(config, raw={**config.raw, "loop": {**config.raw["loop"], "mode": "react"}})
    agent = IdeaAgent(agent_config=config)
    request = RunRequest("pimc", "task", extra={"run_root": str(tmp_path),
                         "context_sources": {"project_rules": False, "code_repositories": False}})
    context = await agent.build_context(request)
    with pytest.raises(ValueError, match="requires reflection mode"):
        await agent.draft(request, context)
    assert not (tmp_path / "agent_traces").exists()
