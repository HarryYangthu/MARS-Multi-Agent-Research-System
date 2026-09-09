"""Human-authored state contracts, never a simulated provider or tool execution."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any
from pathlib import Path

import pytest

from app.harness.agent_loop.executor import apply_loop_cancellation, apply_planned_review_decision, budget_message
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.protocol import parse_review
from app.harness.agent_loop.review_plan import (
    ReviewPlan, ReviewUnit, UnitReviewResult, _plan_structure_errors, completed_plan_decision, collected_reflection_errors,
    finish_review_unit, isolated_unit_input_errors, pack_review_unit, plan_payload, prepare_review_plan,
    review_plan_errors, review_plan_fingerprint, start_review_unit, validate_review_plan_resume,
)
from app.harness.agent_loop.trace import canonical, digest
from app.harness.llm.provider_base import LLMConfig, Message


def _parse(text: str) -> UnitReviewResult:
    decision = parse_review(text)
    return UnitReviewResult(decision, {"human_authored_contract": decision})


def _plan() -> ReviewPlan:
    return ReviewPlan("test.authored_collect_contract.v2", digest("Human-authored state contract."), tuple(
        ReviewUnit(identifier, (Message("user", "Human-authored isolated input " + identifier),),
                   {"type": "object"}, _parse) for identifier in ("unit_a", "unit_b")), failure_mode="collect_units")


def _state(plan: ReviewPlan) -> dict[str, Any]:
    return {"candidate": "Human-authored state contract.", "history": [], "status": "running", "pending": None,
            "counts": {key: 0 for key in ("model_requests", "model_responses", "reflections", "tool_dispatches", "validation_repairs")},
            "usage_complete": False, "review_plan_contract_id": plan.contract_id, "reflection_accepted": False,
            "reviewed_candidate_sha": "", "protocol_output": "", "review_issues": [], "phase_efforts": {},
            "next_phase": "reflect", "feedback": ""}


def _decision(accept: bool, identifier: str) -> UnitReviewResult:
    return _parse(canonical({"accept": accept, "issues": [] if accept else ["Authored negative contract " + identifier],
                             "rationale": "Authored contract rationale " + identifier}))


def _finish(state: dict[str, Any], result: UnitReviewResult, *, limit: int = 2) -> tuple[dict[str, Any], str]:
    state["counts"]["model_requests"] += 1
    start_review_unit(state, [])
    text = canonical(result.decision)
    return apply_planned_review_decision(state, result, response_text=text, response_visible=text, max_reflections=limit)


def test_default_payload_and_fingerprint_remain_byte_identical() -> None:
    default = replace(_plan(), contract_id="test.authored_fail_fast.v1", failure_mode="fail_fast")
    payload = plan_payload(default)
    assert set(payload) == {"contract_id", "candidate_sha256", "units"}
    config, policy = LLMConfig(provider="deepseek", model="deepseek-v4-pro"), AgentLoopPolicy(mode="reflection", trace="full")
    from dataclasses import fields
    model = {field.name: getattr(config, field.name) for field in fields(config) if field.name != "attempt_observer"}
    expected = digest({"base": "original", "review_plan_runtime_version": 1, "review_plan_contract_id": default.contract_id,
                       "model_config_sha256": digest(model)})
    def factory(candidate: str, history: list[dict[str, Any]]) -> ReviewPlan:
        return default
    assert review_plan_fingerprint("original", factory, default.contract_id, config, policy) == expected
    assert review_plan_fingerprint("original", factory, _plan().contract_id, config, policy) != expected
    assert review_plan_fingerprint("original", None, None, config, policy) == "original"


@pytest.mark.parametrize("last_accept", [True, False])
def test_all_unit_opinions_are_collected_once_even_when_last_unit_accepts(last_accept: bool) -> None:
    plan = _plan()
    state = _state(plan)
    assert prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=3)
    first, outcome = _finish(state, _decision(False, "a"))
    assert outcome == "next_unit" and first["decision"]["accept"] is False
    assert state["review_plan"]["status"] == "running" and state["counts"]["reflections"] == 0
    assert state["review_issues"] == [] and state["feedback"] == "" and state["next_phase"] == "reflect"
    assert _plan_structure_errors(state["review_plan"]) == []
    messages, _ = pack_review_unit(plan.units[1], budget=10000, budget_context=budget_message(AgentLoopPolicy(), state["counts"]))
    wire = [message.to_wire() for message in messages]
    assert isolated_unit_input_errors(plan_payload(plan)["units"][1], wire) == []
    assert "Authored negative" not in canonical(wire) and "unit_a" not in canonical(wire)
    last, outcome = _finish(state, _decision(last_accept, "b"))
    aggregate = completed_plan_decision(state["review_plan"])
    assert aggregate is not None and aggregate["accept"] is False
    assert last["decision"]["accept"] is last_accept
    assert outcome == "revision" and state["next_phase"] == "act" and not state["reflection_accepted"]
    assert state["counts"]["reflections"] == 1 and state["counts"]["model_requests"] == 2
    assert state["review_issues"] == aggregate["issues"]
    assert len(aggregate["issues"]) == (1 if last_accept else 2)
    assert aggregate["issues"][0] == "[unit_a] " + first["decision"]["issues"][0]
    assert state["feedback"] == canonical({"required_revision": aggregate["issues"], "review_rationale": aggregate["rationale"],
        "instruction": "Revise the complete candidate to resolve these issues. Do not merely remove warnings."})
    assert _plan_structure_errors(state["review_plan"]) == []
    with pytest.raises(ValueError, match="finished"):
        start_review_unit(state, [])
    # Even a corrupted status marker cannot authorize whole review after a rejection.
    changed = deepcopy(state)
    changed["review_plan"]["status"] = "running"
    with pytest.raises(ValueError, match="every insight"):
        start_review_unit(changed, [])


def test_fail_fast_default_still_returns_first_rejection() -> None:
    plan = replace(_plan(), failure_mode="fail_fast")
    state = _state(plan)
    prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=3)
    record, outcome = _finish(state, _decision(False, "a"))
    assert outcome == "revision" and state["counts"]["reflections"] == 1
    assert state["review_issues"] == record["decision"]["issues"]
    assert state["review_plan"]["next_unit"] == 1 and state["review_plan"]["status"] == "rejected"
    assert _plan_structure_errors(state["review_plan"]) == []


def test_all_positive_units_still_require_whole_review_and_one_reflection() -> None:
    plan = _plan()
    state = _state(plan)
    prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=3)
    for name in ("a", "b"):
        _, outcome = _finish(state, _decision(True, name))
        assert outcome == "next_unit" and state["counts"]["reflections"] == 0
        assert not state["reflection_accepted"]
    assert review_plan_errors(state, state["candidate"], plan.contract_id)
    record, outcome = _finish(state, _decision(True, "whole"))
    assert record["unit_id"] == "__whole_report__" and outcome == "accepted"
    assert state["counts"]["reflections"] == 1 and state["status"] == "passed"
    assert review_plan_errors(state, state["candidate"], plan.contract_id) == []
    assert not apply_loop_cancellation(state)


def test_initial_and_remaining_budget_include_whole_without_automatic_extension() -> None:
    plan = _plan()
    state = _state(plan)
    assert not prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=2)
    assert state["status"] == "budget_exhausted" and state["counts"]["model_requests"] == 0
    state = _state(plan)
    assert prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=3)
    _finish(state, _decision(False, "a"))
    assert not prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=2)
    assert state["status"] == "budget_exhausted" and state["counts"]["reflections"] == 0
    assert len(state["review_plan"]["results"]) == 1 and not state["reflection_accepted"]


def test_invalid_unit_response_never_collects_or_produces_author_revision() -> None:
    plan = _plan()
    state = _state(plan)
    prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=3)
    state["counts"]["model_requests"] = 1
    start_review_unit(state, [])
    failure = _decision(False, "invalid syntax contract")
    finish_review_unit(state, failure, response_text="Human-authored invalid JSON", response_visible="Human-authored invalid JSON", valid=False)
    assert state["review_plan"]["status"] == "failed" and completed_plan_decision(state["review_plan"]) is None
    assert state["counts"]["reflections"] == 0 and not state["reflection_accepted"]
    with pytest.raises(ValueError, match="finished"):
        start_review_unit(state, [])
    assert _plan_structure_errors(state["review_plan"]) == []


def test_known_partial_rejection_survives_cancellation_but_unknown_cannot_replay(tmp_path: Path) -> None:
    plan = _plan()
    state = _state(plan)
    prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=3)
    _finish(state, _decision(False, "a"))
    assert apply_loop_cancellation(state)
    assert state["review_plan"]["status"] == "running" and state["next_phase"] == "reflect"
    state["status"] = "running"  # Pure known-boundary state, not a claimed resumed Agent run.
    assert prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=3)
    state["counts"]["model_requests"] += 1
    start_review_unit(state, [])
    state["pending"] = "model"
    assert apply_loop_cancellation(state) and state["usage_complete"] is False
    with pytest.raises(ValueError, match="outcome unknown"):
        validate_review_plan_resume(state, tmp_path, contract_id=plan.contract_id)
    with pytest.raises(ValueError, match="outcome unknown"):
        prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=3)


def test_last_positive_with_prior_rejection_is_durable_before_cancellation() -> None:
    plan = _plan()
    state = _state(plan)
    prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=3)
    _finish(state, _decision(False, "a"))
    record, outcome = _finish(state, _decision(True, "b"))
    assert record["decision"]["accept"] and outcome == "revision"
    assert apply_loop_cancellation(state)
    assert state["status"] == "interrupted" and state["next_phase"] == "act"
    assert state["review_plan"]["status"] == "rejected" and state["counts"]["reflections"] == 1
    assert _plan_structure_errors(state["review_plan"]) == []


def test_revised_candidate_gets_fresh_units_and_consumes_original_reflection_budget() -> None:
    plan = _plan()
    state = _state(plan)
    prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=6)
    _finish(state, _decision(False, "a"))
    _finish(state, _decision(True, "b"))
    previous = deepcopy(state["review_plan"])
    with pytest.raises(ValueError, match="finished"):
        prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=6)
    state["candidate"] += " Authored revision."
    revised = replace(plan, candidate_sha256=digest(state["candidate"]))
    assert prepare_review_plan(state, revised, contract_id=plan.contract_id, max_model_calls=6)
    assert state["review_plan"]["results"] == [] and state["review_plan"]["next_unit"] == 0
    assert state["review_plan_history"] == [previous] and state["counts"]["reflections"] == 1
    _finish(state, _decision(False, "revision a"))
    _finish(state, _decision(True, "revision b"))
    assert state["status"] == "reflection_rejected" and state["counts"]["reflections"] == 2


def test_failure_mode_cannot_be_changed_inside_an_existing_plan() -> None:
    plan = _plan()
    state = _state(plan)
    prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=3)
    with pytest.raises(ValueError, match="inputs changed"):
        prepare_review_plan(state, replace(plan, failure_mode="fail_fast"), contract_id=plan.contract_id, max_model_calls=3)
    bad = deepcopy(state["review_plan"])
    bad["failure_mode"] = "fail_fast"
    assert _plan_structure_errors(bad)


@pytest.mark.parametrize("version", [2.0, True, "2", None])
def test_explicit_runtime_version_is_an_integer_not_a_coercible_value(version: object) -> None:
    plan = _plan()
    state = _state(plan)
    prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=3)
    changed = deepcopy(state["review_plan"])
    changed["review_plan_runtime_version"] = version
    assert any("invalid explicit review failure mode/version" in error for error in _plan_structure_errors(changed))


def test_aggregate_journal_uses_false_even_if_last_unit_is_true() -> None:
    # Human-authored journal contract; no SDK, provider, tool or real-run claim.
    plan = _plan()
    state = _state(plan)
    prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=3)
    _finish(state, _decision(False, "a"))
    _finish(state, _decision(True, "b"))
    stored = state["review_plan"]
    aggregate = completed_plan_decision(stored)
    assert aggregate is not None
    rows = [{"kind": "review_unit", "event_seq": 10, "request": 2},
            {"kind": "reflection", "event_seq": 11, "accept": False,
             "review_plan_sha256": stored["plan_sha256"], "review_candidate_sha256": stored["candidate_sha256"],
             "visible": aggregate, "visible_sha256": digest(aggregate)}]
    assert collected_reflection_errors(stored, rows) == []
    bad = deepcopy(rows)
    bad[-1].update(accept=True, visible=stored["results"][-1]["decision"],
                   visible_sha256=digest(stored["results"][-1]["decision"]))
    assert collected_reflection_errors(stored, bad)
    assert collected_reflection_errors(stored, rows[:1])
    assert collected_reflection_errors(stored, [*rows, rows[-1]])
    bad = deepcopy(rows)
    bad[-1]["event_seq"] = 9
    assert collected_reflection_errors(stored, bad)
    pending = deepcopy(stored)
    pending["status"] = "running"
    assert collected_reflection_errors(pending, rows)


def test_invalid_later_unit_stops_after_an_earlier_valid_rejection() -> None:
    plan = _plan()
    state = _state(plan)
    prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=3)
    _finish(state, _decision(False, "a"))
    state["counts"]["model_requests"] += 1
    start_review_unit(state, [])
    failure = _decision(False, "invalid later response")
    finish_review_unit(state, failure, response_text="Invalid authored JSON", response_visible="Invalid authored JSON", valid=False)
    assert _plan_structure_errors(state["review_plan"]) == []
    assert state["review_plan"]["status"] == "failed" and completed_plan_decision(state["review_plan"]) is None
    assert state["counts"]["reflections"] == 0 and state["feedback"] == ""
    with pytest.raises(ValueError, match="finished"):
        start_review_unit(state, [])


def test_rejected_unit_does_not_authorize_truncating_the_next_input() -> None:
    plan = _plan()
    state = _state(plan)
    prepare_review_plan(state, plan, contract_id=plan.contract_id, max_model_calls=3)
    _finish(state, _decision(False, "a"))
    with pytest.raises(ValueError, match="no truncation"):
        pack_review_unit(plan.units[1], budget=1, budget_context=budget_message(AgentLoopPolicy(), state["counts"]))
    assert state["counts"]["model_requests"] == 1 and state["counts"]["reflections"] == 0
    assert not state["reflection_accepted"]
