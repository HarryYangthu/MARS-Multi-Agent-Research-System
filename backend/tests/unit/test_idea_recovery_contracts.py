"""Regression checks for error paths and honest evidence exhaustion; no doubles."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

from app.agents.base import RunRequest
from app.agents.idea.focused_agent import FocusedIdeaAgent
from app.agents.idea.focused_research import focused_research_errors
from app.harness.agent_loop.stop import LoopStopView
from app.harness.agent_loop.executor import rejected_batch_feedback
from app.harness.agent_loop.trace import digest
from app.harness.schema.validator import _format_path, get_schema
from app.harness.tools.search.policy import search_policy


def test_nested_missing_field_reports_exact_handoff_path() -> None:
    schema = get_schema("proposal.v1")["properties"]["handoff"]
    errors = list(Draft202012Validator({"properties": {"handoff": schema}}).iter_errors({"handoff": {}}))
    assert "/handoff/target_agent" in [_format_path(error) for error in errors]
    assert "/target_agent" not in [_format_path(error) for error in errors]


def test_required_error_below_array_escapes_json_pointer() -> None:
    schema = {"properties": {"a/b": {"items": {"required": ["x~y"]}}}}
    error = next(Draft202012Validator(schema).iter_errors({"a/b": [{}]}))
    assert _format_path(error) == "/a~1b/0/x~0y"


def test_empty_source_ids_are_missing_evidence_not_duplicate_documents(tmp_path: Path) -> None:
    source = {"source_id": "", "title": "Authored negative validation input",
              "url": "https://example.org/not-retrieved", "decision": "use", "reason": "No retrieval claimed"}
    metadata = {"research_context": {"schema": "idea.research_context.v2", "question": "Check refusal",
        "selection_principles": ["Use real evidence"], "sources": [source, deepcopy(source)],
        "stop_reason": "This is a validation input", "open_questions": [],}}
    for item in metadata["research_context"]["sources"]:
        item["method_pages"] = []
    errors = focused_research_errors(metadata, [], tmp_path)
    assert sum("successful full-text" in error for error in errors) == 2
    assert not any("duplicate" in error for error in errors)
    assert any("no usable method evidence" in error for error in errors)


def test_no_fulltext_stops_before_another_call_after_tool_budget() -> None:
    agent = FocusedIdeaAgent()
    request = RunRequest(project="pimc", user_request="Check honest evidence exhaustion")
    stop = agent.loop_stop_condition(request)
    limit = agent.loop_policy.max_tool_steps
    view = LoopStopView("before_model", "", [], {"tool_dispatches": limit}, "act")
    result = stop(view)
    assert result is not None and result.status == "evidence_unavailable"
    assert result.details["missing"] == ["successful_full_text_reading"]
    assert stop(LoopStopView("before_model", "", [], {"tool_dispatches": limit - 1}, "act")) is None


def test_metadata_failure_and_fallback_fit_declared_timeout() -> None:
    from app.harness.tools.config import tool_config
    policy = search_policy()
    timeout = tool_config("search.arxiv_search").timeout_seconds
    assert policy.arxiv.request_timeout_seconds + policy.openalex.request_timeout_seconds < timeout
    assert policy.arxiv.min_interval_seconds >= 3


def test_rejected_batch_feedback_identifies_prior_failure_and_new_action() -> None:
    import json
    from app.harness.agent_loop.policy import AgentLoopPolicy
    # Human-authored negative feedback input; no service/tool result is faked.
    previous = {"tool": "read", "args": {"path": "absent"}, "ok": False, "error": "file not found"}
    identity = digest({"tool": previous["tool"], "args": previous["args"]})
    actions = [{"tool": "read", "args": {"path": "absent"}}, {"tool": "search", "args": {"query": "new"}}]
    result = json.loads(rejected_batch_feedback(actions, seen={identity: {"retry_allowed": False}},
        history=[previous], policy=AgentLoopPolicy(max_tool_steps=4), used=1))
    assert result["batch_not_executed"] is True and result["over_budget"] is False
    assert result["prior_observations_untrusted"] == [previous]
    assert result["not_executed"] == actions
    assert result["remaining_tool_calls"] == 3
