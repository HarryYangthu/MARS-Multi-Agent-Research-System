"""Host contracts and immutable real failure replay; no provider/tool substitutes."""
from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.harness.agent_loop.completion_recovery import (
    apply_author_empty_completion_recovery, author_empty_completion_recovery, validate_author_empty_recovery_resume,
)
from app.harness.agent_loop.context import pack_context
from app.harness.agent_loop.executor import budget_message, phase_llm_config, truncation_recovery
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.trace import audit_trace, digest
from app.harness.llm.provider_base import LLMCompletionError, LLMConfig, Message


def _contract() -> tuple[LLMCompletionError, dict[str, Any], dict[str, Any], AgentLoopPolicy]:
    # Authored failure metadata tests a pure transition; no response is supplied
    # by a replacement provider, and no successful result is produced.
    error = LLMCompletionError(code="empty_final_content", provider="deepseek", model="configuration-contract",
        finish_reason="stop", empty_final=True, usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})
    response = {"kind": "model_response", "event_seq": 4, "request": 1,
                "rejected": True, "reason": error.reason, "usage": error.usage}
    state: dict[str, Any] = {"next_phase": "act", "status": "model_error", "pending": None, "last_model_error": error.reason,
        "counts": {"model_requests": 1, "model_responses": 1, "protocol_repairs": 0},
        "candidate": "Human-authored transition input", "history": [], "phase_efforts": {},
        "feedback": "Preserve the original revision requirement", "usage_complete": False}
    return error, response, state, AgentLoopPolicy(author_empty_completion_repair_enabled=True)


def test_policy_is_opt_in_and_keeps_default_fingerprint_byte_equivalent() -> None:
    policy = AgentLoopPolicy()
    before = asdict(policy)
    before.pop("author_empty_completion_repair_enabled")
    before.pop("reflection_format_repair_enabled")
    assert policy.fingerprint_data() == before
    enabled = replace(policy, author_empty_completion_repair_enabled=True)
    assert enabled.fingerprint_data()["author_empty_completion_repair_contract_version"] == 1
    assert digest(enabled.fingerprint_data()) != digest(before)
    assert AgentLoopPolicy.from_mapping(asdict(enabled)) == enabled


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_policy_rejects_non_boolean_flag(value: object) -> None:
    with pytest.raises(ValueError, match="must be a boolean"):
        AgentLoopPolicy.from_mapping({"author_empty_completion_repair_enabled": value})


@pytest.mark.parametrize("trace", ["off", "metadata"])
def test_policy_requires_durable_audit(trace: str) -> None:
    with pytest.raises(ValueError, match="full auditable traces"):
        AgentLoopPolicy.from_mapping({"author_empty_completion_repair_enabled": True, "trace": trace})


@pytest.mark.parametrize("change", ["disabled", "review", "unknown_exception", "timeout", "length", "unknown_finish",
    "nonempty", "missing_usage", "invalid_usage", "mismatched_response", "not_rejected", "unknown_model",
    "unknown_tool", "unknown_batch", "unanswered_request", "repair_budget", "model_budget", "visible_response"])
def test_recovery_refuses_unproven_completion_and_never_changes_rejected_state(change: str) -> None:
    error, response, state, policy = _contract()
    supplied: Exception = error
    if change == "disabled":
        policy = AgentLoopPolicy()
    elif change == "review":
        state["next_phase"] = "reflect"
        state["review_plan"] = {"pending_request": {"request": 1, "unit_id": "__whole_report__"}}
    elif change in {"unknown_exception", "timeout"}:
        supplied = RuntimeError("empty_final_content") if change == "unknown_exception" else TimeoutError("stop unknown")
    elif change in {"length", "unknown_finish"}:
        error.reason["finish_reason"] = "length" if change == "length" else None
    elif change == "nonempty":
        error.reason["empty_final"] = False
    elif change == "missing_usage":
        error.usage = None
        response["usage"] = None
    elif change == "invalid_usage":
        assert error.usage is not None
        error.usage["total_tokens"] = 99
    elif change == "mismatched_response":
        response["request"] = 2
    elif change == "not_rejected":
        response["rejected"] = False
    elif change in {"unknown_model", "unknown_tool"}:
        state["pending"] = "model" if change == "unknown_model" else "tool"
    elif change == "unknown_batch":
        state["pending_batch"] = True
    elif change == "unanswered_request":
        state["counts"]["model_responses"] = 0
    elif change == "repair_budget":
        state["counts"]["protocol_repairs"] = policy.max_protocol_repairs
    elif change == "model_budget":
        policy = replace(policy, max_model_calls=1)
    elif change == "visible_response":
        response["visible"] = "Human-authored nonempty protocol input"
    original = deepcopy(state)
    assert apply_author_empty_completion_recovery(supplied, response=response, state=state, policy=policy, effort="high") is None
    assert state == original


def test_recovery_keeps_model_policy_observations_candidate_prior_feedback_and_unknown_usage() -> None:
    error, response, state, policy = _contract()
    original = deepcopy(state)
    config = LLMConfig("deepseek", "configuration-contract", thinking_enabled=True, reasoning_effort="high")
    before = phase_llm_config(config, policy, phase="act", native=False, wire_tools=(), effort_overrides=state["phase_efforts"])
    plan = apply_author_empty_completion_recovery(error, response=response, state=state, policy=policy, effort="high")
    assert plan and plan["reasoning_effort"] == "high"
    assert state["counts"] == {"model_requests": 1, "model_responses": 1, "protocol_repairs": 1}
    assert state["candidate"] == original["candidate"] and state["history"] == original["history"]
    assert state["phase_efforts"] == original["phase_efforts"] and state["usage_complete"] is False
    assert original["feedback"] in state["feedback"] and state["last_model_error"] is None
    assert phase_llm_config(config, policy, phase="act", native=False, wire_tools=(), effort_overrides=state["phase_efforts"]) == before
    assert apply_author_empty_completion_recovery(error, response=response, state=state, policy=policy, effort="high") is None
    validate_author_empty_recovery_resume(state, policy)


def test_existing_truncation_route_is_separate_and_unchanged() -> None:
    error, response, state, policy = _contract()
    error.reason.update(code="output_truncated", finish_reason="length")
    assert author_empty_completion_recovery(error, response=response, state=state, policy=policy, effort="high") is None
    previous = truncation_recovery(error.reason, repairs=0, limit=2, effort="high")
    assert previous and previous["reasoning_effort"] == "low"


def test_new_contract_cannot_resume_unknown_or_unrepaired_empty_failure() -> None:
    _, _, state, policy = _contract()
    with pytest.raises(ValueError, match="automatic replay forbidden"):
        validate_author_empty_recovery_resume(state, policy)
    state.update(last_model_error=None, pending="model")
    with pytest.raises(ValueError, match="automatic replay forbidden"):
        validate_author_empty_recovery_resume(state, policy)
    validate_author_empty_recovery_resume(state, AgentLoopPolicy())  # Disabled legacy policy is unchanged.


@pytest.fixture(scope="module")
def actual_empty_run() -> Iterator[dict[str, Any]]:
    configured = os.environ.get("MARS_TEST_EMPTY_AUTHOR_RUN")
    if not configured:
        pytest.skip("requires actual run 17 empty-response archive; no execution substitute")
    root = Path(configured)
    trace = root / "agent_traces/idea_research/58ff4c98c3cc423bba8909ce746f8ec9"
    delegation = root / "idea/research_delegations/58ff4c98c3cc423bba8909ce746f8ec9"
    paths = [trace / "checkpoint.json", trace / "events.jsonl", trace / "facts.json",
             delegation / "request.json", delegation / "failure.json", root / "input/idea_runtime_profile.v1.json"]
    originals = {path: path.read_bytes() for path in paths}
    assert hashlib.sha256(originals[paths[0]]).hexdigest() == "0e4f3b558560ab25b800c6cea573c5ab77826cfeff35462e352a08d62acf8421"
    assert hashlib.sha256(originals[paths[1]]).hexdigest() == "f46e13c292c118fab7a4b12761aa8cc9f68040c1153635d55afa67c84ff8a6fc"
    assert audit_trace(trace)["consistent"]
    yield {"state": json.loads(originals[paths[0]]), "events": [json.loads(line) for line in originals[paths[1]].splitlines()],
           "profile": json.loads(originals[paths[5]]), "failure": json.loads(originals[paths[4]])}
    assert all(path.read_bytes() == data for path, data in originals.items())


def test_actual_failed_response_preserves_8954_usage_and_all_ten_search_hits(actual_empty_run: dict[str, Any]) -> None:
    original = actual_empty_run["state"]
    state = deepcopy(original)
    response = next(row for row in actual_empty_run["events"] if row["event_seq"] == 13)
    error = LLMCompletionError(**response["reason"], usage=response["usage"])
    old_policy = AgentLoopPolicy.from_mapping(actual_empty_run["profile"]["configuration"]["child"]["loop"])
    policy = replace(old_policy, author_empty_completion_repair_enabled=True)
    assert state["status"] == "model_error" and state["next_phase"] == "act"
    assert response["usage"]["total_tokens"] == 8954 and state["usage"]["total_tokens"] == 13341
    assert actual_empty_run["failure"]["observed_material_counts"]["unique_search_sources"] == 10
    assert author_empty_completion_recovery(error, response=response, state=state, policy=old_policy, effort="high") is None
    plan = apply_author_empty_completion_recovery(error, response=response, state=state, policy=policy, effort="high")
    assert plan and plan["response_event_seq"] == 13
    assert plan["response_metadata_sha256"] == digest({key: response[key] for key in
                                                      ("event_seq", "kind", "request", "rejected", "reason", "usage")})
    assert state["usage"] == original["usage"] and state["usage_complete"] is True
    assert state["history"] == original["history"] and state["candidate"] == original["candidate"] == ""
    assert state["counts"] == {**original["counts"], "protocol_repairs": 1}
    assert '"model_calls":14' in budget_message(policy, state["counts"]).content
    messages, manifest = pack_context([Message("system", "Offline context contract"), budget_message(policy, state["counts"])],
        state["history"], state["feedback"], state["candidate"], budget=policy.input_token_budget,
        observation_chars=policy.observation_chars)
    assert not manifest["omitted_history"] and any("search.openalex_search" in message.content for message in messages)
    assert digest(old_policy.fingerprint_data()) != digest(policy.fingerprint_data())
    # This is an offline next-step plan. No request 3 or new tool observation exists.
    assert state["counts"]["model_requests"] == 2 and state["counts"]["tool_dispatches"] == 1


@pytest.mark.parametrize("boundary", ["repair_budget", "model_budget", "review", "unknown_pending"])
def test_actual_failure_replay_stays_closed_at_budget_and_review_boundaries(actual_empty_run: dict[str, Any], boundary: str) -> None:
    # Deliberately varied host-state contract over actual failed input; this is
    # never written back or represented as a newly executed model response.
    state = deepcopy(actual_empty_run["state"])
    response = next(row for row in actual_empty_run["events"] if row["event_seq"] == 13)
    error = LLMCompletionError(**response["reason"], usage=response["usage"])
    policy = replace(AgentLoopPolicy.from_mapping(actual_empty_run["profile"]["configuration"]["child"]["loop"]),
                     author_empty_completion_repair_enabled=True)
    if boundary == "repair_budget":
        state["counts"]["protocol_repairs"] = policy.max_protocol_repairs
    elif boundary == "model_budget":
        policy = replace(policy, max_model_calls=state["counts"]["model_requests"])
    elif boundary == "review":
        state["next_phase"] = "reflect"
    else:
        state["pending"] = "model"
    before = deepcopy(state)
    assert apply_author_empty_completion_recovery(error, response=response, state=state, policy=policy, effort="high") is None
    assert state == before
    with pytest.raises(ValueError, match="automatic replay forbidden"):
        validate_author_empty_recovery_resume(state, policy)
