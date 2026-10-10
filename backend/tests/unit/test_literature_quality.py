"""Pure admission checks and real on-disk receipts; no providers or tool doubles."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
import json

import pytest
from pydantic import ValidationError

from app.agents.base import RunRequest
from app.agents.idea.focused_agent import FocusedIdeaAgent
from app.agents.idea.literature_quality import publication_key, quality_errors, quality_policy
from app.bridge.idea_input_context import IdeaRequirements
from app.bridge.literature_evidence import literature_evidence
from app.bridge.idea_revision_context import failed_idea_revision_context
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.stop import LoopStopView
from app.harness.agent_loop.trace import LoopTrace
from app.harness.schema.frontmatter_parser import dumps


def test_publication_count_does_not_count_arxiv_formats_or_versions_twice() -> None:
    urls = ["https://arxiv.org/abs/2509.19382v1", "https://arxiv.org/pdf/2509.19382",
            "http://export.arxiv.org/pdf/2509.19382v2.pdf"]
    assert len({publication_key(url) for url in urls}) == 1
    assert publication_key("https://arxiv.org/pdf/2010.01868") != publication_key(urls[0])


def test_quality_overrides_are_independent_of_adopted_source_quota() -> None:
    agent = FocusedIdeaAgent()
    defaults = agent.service_profile_snapshot["research_quality"]
    policy = quality_policy(defaults, {"min_read_papers": 4, "min_sources": 1})
    assert policy["min_read_papers"] == 4 and policy["min_candidates"] == 10
    assert defaults["min_read_papers"] == 3
    with pytest.raises(ValidationError):
        IdeaRequirements.model_validate({"min_read_papers": True})
    with pytest.raises(ValueError):
        quality_policy(defaults, {"min_method_directions": -1})


def test_filled_coverage_does_not_pass_without_real_readings(tmp_path: Path) -> None:
    policy = FocusedIdeaAgent().service_profile_snapshot["research_quality"]
    # A manually authored proposal is input to a pure refusal check. It claims
    # no model/tool execution and cannot supply missing observation evidence.
    source = "source_without_receipt"
    context: dict[str, Any] = {"sources": [], "stop_status": "complete", "coverage": [
        {"axis": axis, "finding": "An unsupported assertion", "source_ids": [source], "remaining_gap": ""}
        for axis in policy["coverage_axes"]], "method_comparison": [
            {"direction": name, "source_ids": [source], "mechanism": "claim", "compatibility": "claim",
             "tradeoff": "claim", "decision": "claim"} for name in ("direction A", "direction B")]}
    errors = quality_errors({"research_context": context}, [], tmp_path, policy)
    assert any("实际候选 0 < 10" in error for error in errors)
    assert any("完整方法阅读 0 < 3" in error for error in errors)
    assert any("完整方法阅读凭据" in error for error in errors)
    assert any("换名称重复计数" in error for error in errors)
    missing = deepcopy(context)
    missing["coverage"].pop()
    assert any("未覆盖所有任务维度" in error for error in quality_errors({"research_context": missing}, [], tmp_path, policy))


def test_evidence_gap_stops_instead_of_repairing_to_fake_completion() -> None:
    agent = FocusedIdeaAgent()
    stop = agent.loop_stop_condition(RunRequest(project="pimc", user_request="Inspect refusal"))
    text = dumps({"research_context": {"stop_status": "evidence_gap", "stop_reason": "No method text available",
                                       "open_questions": ["Missing formula"]}}, "Explicitly incomplete")
    result = stop(LoopStopView("before_model", text, [], {"tool_dispatches": 0}, "act"))
    assert result and result.status == "evidence_unavailable"
    assert result.details["open_questions"] == ["Missing formula"]


def test_authored_proposal_cannot_fabricate_inventory(tmp_path: Path) -> None:
    (tmp_path / "idea").mkdir()
    (tmp_path / "idea/idea_proposal.v1.md").write_text(dumps({"project": "pimc", "research_context": {"sources": [
        {"source_id": "absent", "title": "Human-authored unsupported input", "url": "https://example.org/paper",
         "decision": "use"}]}}, "This is not evidence"))
    view = literature_evidence(tmp_path, "real-temp-input", "pimc")
    assert view["statistics_verified"] is False
    assert view["sources"] == [] and view["counts"]["adopted"] == 0
    assert len(view["warnings"]) == 2
    with pytest.raises(ValueError, match="项目不一致"):
        literature_evidence(tmp_path, "real-temp-input", "other-project")


def test_incomplete_or_inflight_checkpoint_is_not_reused_as_a_model_draft(tmp_path: Path) -> None:
    root = tmp_path / "agent_traces/idea/incomplete"
    root.mkdir(parents=True)
    (root / "checkpoint.json").write_text(json.dumps({"status": "validation_exhausted", "pending": "model"}))
    assert failed_idea_revision_context(tmp_path, "pimc") == {}
    (root / "checkpoint.json").write_text(json.dumps({"status": "validation_exhausted", "pending": None}))
    assert failed_idea_revision_context(tmp_path, "pimc") == {}


def test_active_timer_is_persisted_and_resume_starts_from_saved_usage(tmp_path: Path) -> None:
    state: dict[str, Any] = {"status": "interrupted", "counts": {}, "usage": {}, "usage_complete": True,
             "fingerprint": "input", "pending": None, "active_elapsed_seconds": 12.5}
    trace = LoopTrace(tmp_path, "full")
    trace.emit("started", {})
    trace.snapshot(state)
    elapsed = state["active_elapsed_seconds"]
    assert elapsed >= 12.5
    resumed = json.loads((tmp_path / "checkpoint.json").read_text())
    trace2 = LoopTrace(tmp_path, "full", resume=True)
    trace2.snapshot(resumed)
    assert elapsed <= resumed["active_elapsed_seconds"] < elapsed + 1
    assert json.loads((tmp_path / "facts.json").read_text())["active_elapsed_seconds"] == resumed["active_elapsed_seconds"]
    assert "max_active_seconds" not in AgentLoopPolicy().fingerprint_data()
    assert AgentLoopPolicy(max_active_seconds=1800).fingerprint_data()["max_active_seconds"] == 1800
