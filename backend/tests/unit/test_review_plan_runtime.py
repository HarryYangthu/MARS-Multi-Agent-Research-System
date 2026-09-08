"""Pure planning/transitions over immutable real review archives; no provider double.

Transition tests exercise host state only. Archived responses are never returned
by a replacement provider or presented as a new real agent execution.
"""
from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import asdict, replace
from functools import partial
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.agents.idea.research_review_plan import insight_fields, parse_insight_review
from app.harness.agent_loop.executor import (
    apply_loop_cancellation, apply_planned_review_decision, apply_review_decision, budget_message, phase_llm_config,
)
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.protocol import parse_review
from app.harness.agent_loop.review_plan import (
    ReviewPlan, ReviewUnit, UnitReviewResult, finish_review_unit, isolated_unit_input_errors,
    pack_review_unit, plan_payload, prepare_review_plan, review_plan_errors, review_plan_fingerprint,
    review_plan_trace_errors, review_unit_config, start_review_unit, validate_review_plan_resume,
    validate_review_provider,
)
from app.harness.agent_loop.trace import digest
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.llm.anthropic_provider import AnthropicProvider
from app.harness.llm.gemini_provider import GeminiProvider
from app.harness.llm.openai_provider import DeepSeekProvider

CONTRACT = "test.real_archive_review_plan.v1"


@pytest.fixture(scope="module")
def archive() -> Iterator[dict[str, Any]]:
    configured = os.environ.get("MARS_TEST_INSIGHT_FIELD_COMPONENT")
    if not configured:
        pytest.skip("requires the actual isolated field review component; no replacement model response")
    root = Path(configured)
    paths = [root / name for name in ("request.json", "response.json", "selected_original_fields.json", "response_contract.json")]
    original = {path: path.read_bytes() for path in paths}
    request = json.loads(original[paths[0]])
    assert hashlib.sha256(original[paths[1]]).hexdigest() == "287323fd271f16e233c8ed9cb163987ba54e81d443fba1896dab10e61be824af"
    run = Path(request["source_run_root"])
    report = run / "idea/research_delegations" / request["source_child"] / "report.md"
    original[report] = report.read_bytes()
    trace = run / "agent_traces/idea_research" / request["source_child"]
    for path in (trace / "events.jsonl", trace / "checkpoint.json", trace / "facts.json"):
        original[path] = path.read_bytes()
    yield {"root": root, "request": request, "response": json.loads(original[paths[1]]),
           "selected": json.loads(original[paths[2]]), "schema": json.loads(original[paths[3]]),
           "candidate": original[report].decode(), "trace": trace,
           "checkpoint": json.loads(original[trace / "checkpoint.json"])}
    assert all(path.read_bytes() == data for path, data in original.items())


def _plan(archive: dict[str, Any]) -> ReviewPlan:
    messages = tuple(Message(role=row["role"], content=row["content"]) for row in archive["request"]["wire_request"]["messages"])
    unit = ReviewUnit("insight:I2", messages, archive["schema"],
                      partial(parse_insight_review, fields=insight_fields(archive["selected"]["insight"])),
                      tuple(archive["request"]["source_rows"]))
    return ReviewPlan(CONTRACT, digest(archive["candidate"]), (unit,))


def _state(archive: dict[str, Any]) -> dict[str, Any]:
    return {"candidate": archive["candidate"], "history": [], "status": "running", "pending": None,
            "counts": {key: 0 for key in archive["checkpoint"]["counts"]},
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, "usage_complete": False,
            "review_plan_contract_id": CONTRACT, "reflection_accepted": False,
            "reviewed_candidate_sha": "", "protocol_output": "", "review_issues": [],
            "phase_efforts": {}, "next_phase": "reflect", "feedback": ""}


def test_baseline_fingerprint_is_unchanged_and_planned_review_has_exact_model_config() -> None:
    config = LLMConfig("deepseek", "deepseek-v4-pro", thinking_enabled=True, reasoning_effort="high")
    policy = AgentLoopPolicy(mode="reflection")
    assert review_plan_fingerprint("historical", None, None, config, policy) == "historical"
    # A factory is not called by fingerprinting and produces no execution data.
    def unused_factory(candidate: str, history: list[dict[str, Any]]) -> ReviewPlan:
        raise AssertionError("fingerprinting must not build or execute a review")
    base = review_plan_fingerprint("historical", unused_factory, CONTRACT, config, policy)
    for changed in (replace(config, thinking_enabled=False), replace(config, max_tokens=8192),
                    replace(config, temperature=0.2), replace(config, max_retries=0)):
        assert review_plan_fingerprint("historical", unused_factory, CONTRACT, changed, policy) != base
    with pytest.raises(ValueError, match="requires"):
        review_plan_fingerprint("historical", None, CONTRACT, config, policy)
    with pytest.raises(ValueError, match="require reflection"):
        review_plan_fingerprint("historical", unused_factory, CONTRACT, config, replace(policy, mode="react"))
    with pytest.raises(ValueError, match="full"):
        review_plan_fingerprint("historical", unused_factory, CONTRACT, config, replace(policy, trace="metadata"))


def test_unit_config_preserves_real_reflection_thinking_but_has_one_attempt_and_no_tools() -> None:
    author = LLMConfig("deepseek", "deepseek-v4-pro", thinking_enabled=False, max_tokens=32768, max_retries=3)
    policy = AgentLoopPolicy(mode="reflection", reflection_thinking_enabled=True, reflection_reasoning_effort="high")
    reflected = phase_llm_config(author, policy, phase="reflect", native=True, wire_tools=(), effort_overrides={})
    unit = review_unit_config(reflected)
    assert unit.thinking_enabled is True and unit.reasoning_effort == "high" and unit.max_tokens == 32768
    assert unit.max_retries == 0 and unit.json_mode and unit.tools == ()
    assert asdict(reflected) | {"max_retries": 0, "json_mode": True, "tools": ()} == asdict(unit)
    assert author.max_retries == 3 and author.thinking_enabled is False


def test_unaccounted_provider_adapters_are_explicitly_rejected_without_any_client_or_call() -> None:
    # Construct actual adapters only for their local configuration contract.
    # No SDK client is created and no provider.complete method is invoked.
    deepseek = DeepSeekProvider(api_key="configuration-input-not-a-key")
    validate_review_provider(deepseek, configured_provider="deepseek")
    assert deepseek._client is None
    with pytest.raises(ValueError, match="actual adapter"):
        validate_review_provider(deepseek, configured_provider="openai")
    for provider in (AnthropicProvider(api_key="configuration-input-not-a-key"),
                     GeminiProvider(api_key="configuration-input-not-a-key")):
        with pytest.raises(ValueError, match="auditable"):
            validate_review_provider(provider, configured_provider=provider.name)
        assert provider._client is None


def test_actual_component_all_original_fields_and_pages_survive_input_isolation(archive: dict[str, Any]) -> None:
    plan = _plan(archive)
    state = _state(archive)
    assert prepare_review_plan(state, plan, contract_id=CONTRACT, max_model_calls=2)
    unit = plan.units[0]
    messages, manifest = pack_review_unit(unit, budget=256000, budget_context=budget_message(AgentLoopPolicy(), state["counts"]))
    assert [message.to_wire() for message in messages[:-2]] == archive["request"]["wire_request"]["messages"]
    assert manifest["complete_input_sha256"] == digest([message.to_wire() for message in messages])
    assert manifest["omitted_history"] == manifest["compressed_history"] == []
    assert isolated_unit_input_errors(plan_payload(plan)["units"][0], [message.to_wire() for message in messages]) == []
    assert state["counts"]["model_requests"] == 0 and state["usage_complete"] is False


@pytest.mark.parametrize("mutation", ["extra_insight", "old_acceptance", "changed_page", "extra_budget_text", "schema_prefix_only"])
def test_actual_wire_input_rejects_leaks_or_truncation_even_with_self_consistent_hashes(archive: dict[str, Any], mutation: str) -> None:
    plan = _plan(archive)
    messages, _ = pack_review_unit(plan.units[0], budget=256000, budget_context=budget_message(AgentLoopPolicy(), _state(archive)["counts"]))
    wire = [message.to_wire() for message in messages]
    if mutation in {"extra_insight", "old_acceptance"}:
        wire.insert(-2, {"role": "user", "content": mutation})
    elif mutation == "changed_page":
        wire[0]["content"] = wire[0]["content"][:-1]
    elif mutation == "extra_budget_text":
        wire[-2]["content"] += " additional context"
    else:
        wire[-1]["content"] = wire[-1]["content"][:100]
    assert isolated_unit_input_errors(plan_payload(plan)["units"][0], wire)


def test_full_unit_must_fit_without_silent_compression(archive: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="no truncation"):
        pack_review_unit(_plan(archive).units[0], budget=100, budget_context=budget_message(AgentLoopPolicy(), _state(archive)["counts"]))


def test_reserve_all_units_and_whole_before_spending_even_one_call(archive: dict[str, Any]) -> None:
    state = _state(archive)
    assert not prepare_review_plan(state, _plan(archive), contract_id=CONTRACT, max_model_calls=1)
    assert state["status"] == "budget_exhausted" and not state["reflection_accepted"]
    assert state["counts"]["model_requests"] == 0 and state["review_plan"]["results"] == []


def test_actual_field_response_is_a_rejection_with_all_five_checks_retained(archive: dict[str, Any]) -> None:
    plan = _plan(archive)
    result = plan.units[0].parse_response(archive["response"]["text"])
    assert not result.decision["accept"] and len(result.decision["issues"]) == 1
    assert len(result.details["checks"]) == 5
    state = _state(archive)
    prepare_review_plan(state, plan, contract_id=CONTRACT, max_model_calls=2)
    state["counts"]["model_requests"] += 1  # Pure host transition, not a provider call.
    start_review_unit(state, list(plan.units[0].messages))
    record = finish_review_unit(state, result, response_text=archive["response"]["text"], response_visible=archive["response"]["text"])
    assert record["details"] == result.details and state["review_plan"]["status"] == "rejected"
    assert not state["reflection_accepted"] and state["counts"]["reflections"] == 0
    assert apply_review_decision(state, result.decision, format_repair=False, max_reflections=2) == "revision"
    assert state["counts"]["reflections"] == 1 and state["counts"]["model_requests"] == 1
    assert state["next_phase"] == "act" and not state["reflection_accepted"]
    with pytest.raises(ValueError, match="automatic rereview forbidden"):
        prepare_review_plan(state, plan, contract_id=CONTRACT, max_model_calls=36)
    assert review_plan_errors(state, archive["candidate"], CONTRACT)


def test_unknown_sent_request_cannot_be_resent_or_claimed_passed(archive: dict[str, Any], tmp_path: Path) -> None:
    state = _state(archive)
    plan = _plan(archive)
    prepare_review_plan(state, plan, contract_id=CONTRACT, max_model_calls=2)
    state["counts"]["model_requests"] += 1
    start_review_unit(state, list(plan.units[0].messages))
    # Even a provider error that cleared generic pending cannot erase the unit.
    state["pending"] = None
    with pytest.raises(ValueError, match="outcome unknown"):
        validate_review_plan_resume(state, tmp_path, contract_id=CONTRACT)
    with pytest.raises(ValueError, match="outcome unknown"):
        prepare_review_plan(state, plan, contract_id=CONTRACT, max_model_calls=2)
    with pytest.raises(ValueError, match="already started"):
        start_review_unit(state, list(plan.units[0].messages))
    assert state["counts"]["model_requests"] == 1 and state["usage_complete"] is False


def test_changed_evidence_or_units_cannot_resume_the_same_candidate(archive: dict[str, Any]) -> None:
    state = _state(archive)
    plan = _plan(archive)
    prepare_review_plan(state, plan, contract_id=CONTRACT, max_model_calls=2)
    altered = replace(plan, units=(replace(plan.units[0], evidence_bindings=()),))
    with pytest.raises(ValueError, match="inputs changed"):
        prepare_review_plan(state, altered, contract_id=CONTRACT, max_model_calls=36)


def test_revision_keeps_prior_results_unknown_usage_and_consumed_budget(archive: dict[str, Any]) -> None:
    state = _state(archive)
    plan = _plan(archive)
    prepare_review_plan(state, plan, contract_id=CONTRACT, max_model_calls=3)
    state["counts"]["model_requests"] = 1
    start_review_unit(state, list(plan.units[0].messages))
    actual = plan.units[0].parse_response(archive["response"]["text"])
    record = finish_review_unit(state, actual, response_text=archive["response"]["text"], response_visible=archive["response"]["text"])
    apply_review_decision(state, actual.decision, format_repair=False, max_reflections=2)
    # A changed document is only a pure planning input here, not an Agent answer.
    state["candidate"] += "\n"
    changed = replace(plan, candidate_sha256=digest(state["candidate"]))
    assert not prepare_review_plan(state, changed, contract_id=CONTRACT, max_model_calls=2)
    assert state["counts"]["model_requests"] == 1 and state["counts"]["reflections"] == 1
    assert state["usage_complete"] is False and state["review_plan"]["results"] == []
    assert state["review_plan_history"][0]["results"] == [record]
    state["candidate"] = archive["candidate"]
    with pytest.raises(ValueError, match="already had a review plan"):
        prepare_review_plan(state, plan, contract_id=CONTRACT, max_model_calls=36)


def test_malformed_actual_review_is_a_negative_record_not_format_repair(archive: dict[str, Any]) -> None:
    plan = _plan(archive)
    broken = archive["response"]["text"].rstrip()[:-1]
    with pytest.raises(ValueError) as caught:
        plan.units[0].parse_response(broken)
    state = _state(archive)
    prepare_review_plan(state, plan, contract_id=CONTRACT, max_model_calls=2)
    state["counts"]["model_requests"] = 1
    start_review_unit(state, list(plan.units[0].messages))
    invalid = UnitReviewResult({"accept": False, "issues": [str(caught.value)], "rationale": "Response contract failed."},
                               {"protocol_error": str(caught.value)})
    record = finish_review_unit(state, invalid, response_text=broken, response_visible=broken, valid=False)
    assert record["valid"] is False and record["response_sha256"] == digest(broken)
    assert state["review_plan"]["status"] == "failed" and not state["reflection_accepted"]
    assert state["counts"]["model_requests"] == 1 and state["counts"]["reflections"] == 0
    with pytest.raises(ValueError, match="automatic rereview forbidden"):
        prepare_review_plan(state, plan, contract_id=CONTRACT, max_model_calls=36)


def test_positive_archived_review_is_only_a_precheck_until_whole_review(archive: dict[str, Any]) -> None:
    events = [json.loads(line) for line in (archive["trace"] / "events.jsonl").read_text().splitlines()]
    response = next(row for row in events if row["kind"] == "model_response" and row["request"] == 7)
    text = response["visible"] if isinstance(response["visible"], str) else response["visible"]["text"]
    actual = parse_review(text)
    assert actual["accept"] is True
    state = _state(archive)
    plan = _plan(archive)
    prepare_review_plan(state, plan, contract_id=CONTRACT, max_model_calls=2)
    state["counts"]["model_requests"] = 1
    start_review_unit(state, list(plan.units[0].messages))
    # Apply the real accepted review only to the generic state-transition
    # function, never its field parser and never an executing provider.
    finish_review_unit(state, UnitReviewResult(actual, {"review": actual}), response_text=text, response_visible=response["visible"])
    assert state["review_plan"]["next_unit"] == 1 and state["review_plan"]["status"] == "running"
    assert not state["reflection_accepted"] and state["counts"]["reflections"] == 0
    assert prepare_review_plan(state, plan, contract_id=CONTRACT, max_model_calls=2)
    assert review_plan_errors(state, archive["candidate"], CONTRACT)
    state["counts"]["model_requests"] = 2
    pending = start_review_unit(state, [])
    assert pending["unit_id"] == "__whole_report__"
    finish_review_unit(state, UnitReviewResult(actual, {"review": actual}), response_text=text, response_visible=response["visible"])
    assert not state["reflection_accepted"]  # Only the ordinary final reducer accepts.
    apply_review_decision(state, actual, format_repair=False, max_reflections=2)
    assert review_plan_errors(state, archive["candidate"], CONTRACT) == []
    assert state["counts"]["reflections"] == 1 and state["counts"]["model_requests"] == 2
    # This in-memory transition is deliberately not a real trace. It cannot be
    # promoted to a receipt by pointing at the original baseline events.
    assert review_plan_errors(state, archive["candidate"], CONTRACT, trace_root=archive["trace"])


def test_original_whole_archive_cannot_gain_a_field_review_claim(archive: dict[str, Any]) -> None:
    state = deepcopy(archive["checkpoint"])
    assert state["reflection_accepted"] is True
    assert review_plan_errors(state, archive["candidate"], CONTRACT) == ["required review plan is absent"]
    assert review_plan_trace_errors(state, archive["trace"])


def test_actual_rejection_commits_revision_phase_before_notification_cancellation(archive: dict[str, Any]) -> None:
    state = _state(archive)
    plan = _plan(archive)
    prepare_review_plan(state, plan, contract_id=CONTRACT, max_model_calls=3)
    state["counts"]["model_requests"] = 1
    start_review_unit(state, list(plan.units[0].messages))
    actual = plan.units[0].parse_response(archive["response"]["text"])
    record, outcome = apply_planned_review_decision(state, actual, response_text=archive["response"]["text"],
        response_visible=archive["response"]["text"], max_reflections=2)
    assert outcome == "revision" and state["review_plan"]["status"] == "rejected"
    assert state["next_phase"] == "act" and state["counts"]["reflections"] == 1
    assert state["review_issues"] == actual.decision["issues"]
    # Exercise the real synchronous cancellation transition; no progress sink or
    # successful provider callback is replaced. The loop snapshots these facts
    # and emits reflection before its first notification await.
    assert apply_loop_cancellation(state)
    assert state["status"] == "interrupted" and state["next_phase"] == "act"
    assert state["review_plan"]["pending_request"] is None
    assert state["review_plan"]["results"] == [record] and state["counts"]["reflections"] == 1


def test_completed_whole_review_is_not_downgraded_by_late_cancellation(archive: dict[str, Any]) -> None:
    events = [json.loads(line) for line in (archive["trace"] / "events.jsonl").read_text().splitlines()]
    response = next(row for row in events if row["kind"] == "model_response" and row["request"] == 7)
    visible = response["visible"]
    text = visible if isinstance(visible, str) else visible["text"]
    actual = UnitReviewResult(parse_review(text), {"review": parse_review(text)})
    state = _state(archive)
    plan = _plan(archive)
    prepare_review_plan(state, plan, contract_id=CONTRACT, max_model_calls=2)
    state["counts"]["model_requests"] = 1
    start_review_unit(state, list(plan.units[0].messages))
    _, outcome = apply_planned_review_decision(state, actual, response_text=text, response_visible=visible, max_reflections=2)
    assert outcome == "next_unit" and not state["reflection_accepted"]
    assert apply_loop_cancellation(state)
    assert state["review_plan"]["next_unit"] == 1 and state["next_phase"] == "reflect"
    # Resume only the pure host state after a completely recorded positive unit.
    # No archived response is returned by a provider or claimed as a new run.
    state["status"] = "running"
    assert prepare_review_plan(state, plan, contract_id=CONTRACT, max_model_calls=2)
    state["counts"]["model_requests"] = 2
    start_review_unit(state, [])
    _, outcome = apply_planned_review_decision(state, actual, response_text=text, response_visible=visible, max_reflections=2)
    assert outcome == "accepted" and state["status"] == "passed" and state["reflection_accepted"]
    before = deepcopy(state)
    assert not apply_loop_cancellation(state)
    assert state == before and state["counts"]["reflections"] == 1


def test_cancellation_while_request_is_unknown_still_marks_usage_incomplete(archive: dict[str, Any]) -> None:
    state = _state(archive)
    state["pending"] = "model"
    state["usage_complete"] = True
    assert apply_loop_cancellation(state)
    assert state["status"] == "interrupted" and state["usage_complete"] is False
    assert not state["reflection_accepted"]


def test_notification_cancellation_cannot_reopen_exhausted_reflection_budget(archive: dict[str, Any]) -> None:
    state = _state(archive)
    plan = _plan(archive)
    prepare_review_plan(state, plan, contract_id=CONTRACT, max_model_calls=3)
    state["counts"]["model_requests"] = 1
    start_review_unit(state, list(plan.units[0].messages))
    actual = plan.units[0].parse_response(archive["response"]["text"])
    apply_planned_review_decision(state, actual, response_text=archive["response"]["text"],
        response_visible=archive["response"]["text"], max_reflections=1)
    assert state["status"] == "reflection_rejected" and state["counts"]["reflections"] == 1
    before = deepcopy(state)
    assert not apply_loop_cancellation(state)
    assert state == before and not state["reflection_accepted"]
