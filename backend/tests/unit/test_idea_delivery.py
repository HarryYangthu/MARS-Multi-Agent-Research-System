"""Contract and real-file tests; authored documents are parser inputs, not Agent results."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.agents.base import RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.delivery import delivery_errors, progress_message, progress_sink, resolve_pointer
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


def test_candidate_progress_never_claims_acceptance() -> None:
    message = progress_message({"kind": "candidate", "text": "---\nhuman_summary: 这是待验收的研究方案。\n---\n"})
    assert message.startswith("候选方案，尚待验收：")


def test_english_tool_explanations_have_a_chinese_factual_fallback() -> None:
    assert progress_message({"kind": "action", "tool": "search.arxiv_search", "reason": "Search more papers"}) == "正在检索相关论文。"
