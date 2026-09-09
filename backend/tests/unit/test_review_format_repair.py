"""Pure review contracts and optional immutable real archive replay; no model substitute."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.harness.agent_loop.context import pack_context
from app.harness.agent_loop.executor import (
    apply_review_decision, missing_review_evidence, phase_llm_config, reflection_instruction,
    validate_reflection_format_repair,
)
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.protocol import (
    DuplicateJSONKeyError, ReviewConflictError, invalid_output_context, is_review_format_error, parse_review,
)
from app.harness.agent_loop.trace import canonical, digest
from app.harness.llm.openai_provider import DeepSeekProvider, OpenAIProvider
from app.harness.llm.provider_base import LLMConfig, Message


def test_policy_is_explicit_and_default_fingerprint_keeps_old_contract() -> None:
    old = {
        "protocol": "json_actions", "mode": "react", "trace": "full",
        "reflection_reasoning_effort": None, "reflection_thinking_enabled": None,
        "max_model_calls": 36, "max_tool_steps": 18, "max_protocol_repairs": 4,
        "max_validation_repairs": 6, "max_reflections": 3, "input_token_budget": 24000,
        "observation_chars": 6000,
    }
    default = AgentLoopPolicy.from_mapping(old)
    assert not default.reflection_format_repair_enabled
    assert default.fingerprint_data() == old
    assert digest(default.fingerprint_data()) == digest(old)
    enabled = replace(default, reflection_format_repair_enabled=True)
    assert enabled.fingerprint_data()["reflection_format_repair_contract_version"] == 1
    assert digest(enabled.fingerprint_data()) != digest(old)
    assert AgentLoopPolicy.from_mapping(asdict(enabled)) == enabled


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_policy_rejects_non_boolean_enablement(value: object) -> None:
    with pytest.raises(ValueError, match="reflection_format_repair_enabled must be a boolean"):
        AgentLoopPolicy.from_mapping({"reflection_format_repair_enabled": value})


@pytest.mark.parametrize("text,eligible", [
    ('{"accept":false,"issues":["authored issue"],"rationale":"incomplete"', True),
    ('{"accept":false,"issues":[],"rationale":"a","accept":true}', True),
    ('{"accept":false,"issues":[],"rationale":"a"} {}', True),
    ('{"accept":false,"issues":[],"rationale":"a"}', False),
    ('{"accept":"false","issues":["issue"],"rationale":"a"}', False),
    ('{"accept":false,"issues":["issue"]}', False),
    ('{"accept":false,"issues":[3],"rationale":"a"}', False),
    ('{"accept":false,"issues":["issue"],"rationale":NaN}', False),
    ('{"accept":true,"issues":["unresolved blocker"],"rationale":"a"}', False),
])
def test_only_syntax_and_duplicate_keys_enable_format_repair(text: str, eligible: bool) -> None:
    # Authored invalid parser inputs; none is used as a provider completion.
    with pytest.raises(ValueError) as caught:
        parse_review(text)
    assert is_review_format_error(caught.value) is eligible
    with pytest.raises(ValueError):
        parse_review(text)


def test_duplicate_keys_remain_strict_and_conflicts_keep_the_original_issues() -> None:
    with pytest.raises(DuplicateJSONKeyError, match="duplicate JSON key: accept"):
        parse_review('{"accept":false,"accept":false,"issues":["issue"],"rationale":"a"}')
    with pytest.raises(ReviewConflictError) as caught:
        parse_review('{"accept":true,"issues":["unresolved blocker"],"rationale":"a"}')
    assert caught.value.review["issues"] == ["unresolved blocker"]
    assert not is_review_format_error(caught.value)


def _state() -> dict[str, Any]:
    # Human-authored state-transition input, not a checkpoint or execution receipt.
    return {"candidate": "Human-authored candidate for a transition contract", "history": [],
            "counts": {"reflections": 0, "model_requests": 4, "protocol_repairs": 2},
            "protocol_output": "Human-authored malformed review", "review_format_repair_pending": True,
            "reflection_accepted": False, "feedback": "Fix malformed JSON", "status": "running",
            "next_phase": "reflect", "phase_efforts": {"reflect": "low", "act": "low"},
            "review_issues": ["prior blocker"], "reviewed_candidate_sha": "previous candidate digest"}


def test_formatted_acceptance_requires_a_fresh_normal_review_without_resetting_budgets() -> None:
    state = _state()
    before = deepcopy(state)
    decision = {"accept": True, "issues": [], "rationale": "Human-authored acceptance contract"}
    outcome = apply_review_decision(state, decision, format_repair=True, max_reflections=1)
    assert outcome == "independent_review"
    assert state["status"] == "running" and state["next_phase"] == "reflect"
    assert state["reflection_accepted"] is False
    assert state["counts"] == before["counts"]
    assert state["candidate"] == before["candidate"] and state["history"] == before["history"]
    assert state["review_issues"] == before["review_issues"]
    assert state["reviewed_candidate_sha"] == before["reviewed_candidate_sha"]
    assert state["protocol_output"] == state["feedback"] == ""
    assert state["review_format_repair_pending"] is False
    assert state["phase_efforts"] == {"act": "low"}
    normal = phase_llm_config(LLMConfig(provider="deepseek", model="configuration-contract", thinking_enabled=False),
                              AgentLoopPolicy(reflection_format_repair_enabled=True,
                                              reflection_thinking_enabled=True, reflection_reasoning_effort="high"),
                              phase=state["next_phase"], native=True, wire_tools=(),
                              effort_overrides=state["phase_efforts"],
                              review_format_repair=state["review_format_repair_pending"])
    assert normal.thinking_enabled is True and normal.reasoning_effort == "high"
    assert normal.json_mode and not normal.tools and "review_format_repair" not in normal.extra
    messages, manifest = pack_context([reflection_instruction("Scientific contract")], [], state["feedback"],
                                      state["candidate"], budget=4000, observation_chars=512,
                                      reviewing=True, review_issues=state["review_issues"])
    assert not manifest["prior_review_issues_visible"]
    assert all("Human-authored acceptance contract" not in m.content and "prior blocker" not in m.content
               and "malformed review" not in m.content for m in messages)


@pytest.mark.parametrize("format_repair", [False, True])
def test_rejection_keeps_issues_and_consumes_the_existing_review_budget(format_repair: bool) -> None:
    state = _state()
    decision = {"accept": False, "issues": ["Human-authored unresolved blocker"], "rationale": "Contract reason"}
    outcome = apply_review_decision(state, decision, format_repair=format_repair, max_reflections=2)
    assert outcome == "revision"
    assert state["next_phase"] == "act" and state["status"] == "running"
    assert not state["reflection_accepted"]
    assert state["review_issues"] == decision["issues"]
    assert state["reviewed_candidate_sha"] == digest(state["candidate"])
    assert state["counts"] == {"reflections": 1, "model_requests": 4, "protocol_repairs": 2}
    assert json.loads(state["feedback"])["required_revision"] == decision["issues"]
    apply_review_decision(state, decision, format_repair=format_repair, max_reflections=2)
    assert state["status"] == "reflection_rejected"


def test_normal_acceptance_contract_is_unchanged() -> None:
    state = _state()
    decision = {"accept": True, "issues": [], "rationale": "Human-authored transition contract"}
    assert apply_review_decision(state, decision, format_repair=False, max_reflections=1) == "accepted"
    assert state["status"] == "passed" and state["reflection_accepted"]
    assert state["counts"]["reflections"] == 1


def test_actual_deepseek_request_kwargs_disable_thinking_and_inherited_effort_only_for_repair() -> None:
    # Only inspect real provider serialization; never initialize its client or call a model.
    provider = DeepSeekProvider(api_key="configuration-input-not-a-key", default_reasoning_effort="high")
    config = LLMConfig(provider="deepseek", model="configuration-contract", thinking_enabled=False,
                       reasoning_effort="high", extra={"existing": "preserved"})
    policy = AgentLoopPolicy(reflection_thinking_enabled=True, reflection_reasoning_effort="high",
                             reflection_format_repair_enabled=True)
    tools = ({"type": "function", "function": {"name": "contract"}},)
    options: dict[str, Any] = {"native": True, "wire_tools": tools, "effort_overrides": {"reflect": "low"}}
    repair = phase_llm_config(config, policy, phase="reflect", review_format_repair=True, **options)
    wire = provider._request_kwargs([Message("user", "Authored serialization input")], repair)
    assert wire["extra_body"] == {"thinking": {"type": "disabled"}}
    assert wire["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in wire and "tools" not in wire
    assert "review_format_repair" not in canonical(wire)
    assert repair.extra == {"existing": "preserved", "review_format_repair": True}
    assert config.extra == {"existing": "preserved"}
    for phase in ("act", "reflect"):
        enabled = phase_llm_config(config, policy, phase=phase, **options)
        disabled = phase_llm_config(config, replace(policy, reflection_format_repair_enabled=False), phase=phase, **options)
        assert asdict(enabled) == asdict(disabled)
        normal_wire = provider._request_kwargs([], enabled)
        assert normal_wire["reasoning_effort"] == ("high" if phase == "act" else "low")
    resumed = phase_llm_config(config, policy, phase="reflect", native=True, wire_tools=tools, effort_overrides={})
    assert provider._request_kwargs([], resumed)["extra_body"] == {"thinking": {"type": "enabled"}}
    assert provider._request_kwargs([], resumed)["reasoning_effort"] == "high"
    assert provider._client is None


@pytest.mark.parametrize("phase,enabled,provider", [("act", True, "deepseek"), ("reflect", False, "deepseek"),
                                                   ("reflect", True, "openai"), ("reflect", True, "zhipu")])
def test_repair_config_rejects_ineligible_modes(phase: str, enabled: bool, provider: str) -> None:
    with pytest.raises(ValueError, match="requires reflection, explicit policy enablement and DeepSeek"):
        phase_llm_config(LLMConfig(provider=provider, model="configuration-contract"),
                         AgentLoopPolicy(reflection_format_repair_enabled=enabled), phase=phase,
                         native=True, wire_tools=(), effort_overrides={}, review_format_repair=True)


@pytest.mark.parametrize("thinking", [False, None])
def test_enabled_repair_requires_thinking_in_the_normal_review(thinking: bool | None) -> None:
    config = LLMConfig(provider="deepseek", model="configuration-contract", thinking_enabled=thinking)
    policy = AgentLoopPolicy(reflection_format_repair_enabled=True)
    with pytest.raises(ValueError, match="requires enabled thinking for normal reflection"):
        validate_reflection_format_repair(config, policy)
    validate_reflection_format_repair(config, replace(policy, reflection_thinking_enabled=True))
    validate_reflection_format_repair(config, replace(policy, reflection_format_repair_enabled=False))
    validate_reflection_format_repair(replace(config, thinking_enabled=True), policy)


def test_provider_marker_does_not_change_other_providers_or_thinking_reviews() -> None:
    config = LLMConfig(provider="deepseek", model="configuration-contract", thinking_enabled=True,
                       extra={"review_format_repair": True})
    deepseek = DeepSeekProvider(api_key="configuration-input-not-a-key", default_reasoning_effort="high")
    openai = OpenAIProvider(api_key="configuration-input-not-a-key")
    assert deepseek._request_kwargs([], config)["reasoning_effort"] == "high"
    assert openai._request_kwargs([], replace(config, provider="openai", thinking_enabled=False,
                                              reasoning_effort="high"))["reasoning_effort"] == "high"
    assert deepseek._client is None and openai._client is None


def test_real_run10_review_failures_keep_full_evidence_and_original_rejection() -> None:
    trace_value = os.environ.get("MARS_TEST_REVIEW_TRACE")
    if not trace_value:
        pytest.skip("requires actual run10 lead trace containing original requests and responses 5/6/7")
    trace = Path(trace_value)
    paths = (trace / "events.jsonl", trace / "checkpoint.json", trace.parents[2] / "input/request.json")
    originals = {path: path.read_bytes() for path in paths}
    events = [json.loads(line) for line in originals[paths[0]].decode().splitlines()]
    requests = {e["request"]: e for e in events if e["kind"] == "model_request"}
    responses = {e["request"]: e for e in events if e["kind"] == "model_response" and "visible" in e}
    initial = json.loads(originals[paths[2]])
    policy = replace(AgentLoopPolicy.from_mapping(initial["scenario"]["loop"]), reflection_format_repair_enabled=True)
    history = [e["visible"] for e in events if e["kind"] == "observation"
               and e["event_seq"] < requests[5]["event_seq"]]
    required_tools = tuple(item["tool"] for item in history if any(
        m["content"] == "[untrusted complete review Observation]\n" + canonical(item)
        for m in requests[5]["visible"]))
    assert history and required_tools
    provider = DeepSeekProvider(api_key="archive-serialization-not-a-key", default_reasoning_effort="high")
    candidate_message = next(m for m in requests[5]["visible"]
                             if m["content"].startswith("[untrusted current candidate;"))
    candidate = candidate_message["content"].split("\n", 1)[1]
    for previous in (5, 6):
        response = responses[previous]
        assert digest(response["visible"]) == response["visible_sha256"]
        with pytest.raises(ValueError) as caught:
            parse_review(response["visible"]["text"])
        assert is_review_format_error(caught.value)
        assert isinstance(caught.value, DuplicateJSONKeyError if previous == 5 else json.JSONDecodeError)
        request = requests[previous + 1]
        pinned: list[Message] = []
        for row in request["visible"]:
            if row["content"].startswith("[untrusted previous model output;"):
                assert json.loads(row["content"].split("\n", 1)[1])["invalid_output"] == canonical(response["visible"])
                break
            pinned.append(Message(**row))
        raw = canonical(response["visible"])
        repair_context = invalid_output_context(raw, native=False)
        assert json.loads(repair_context.content.split("\n", 1)[1])["invalid_output"] == raw
        pinned += [repair_context, reflection_instruction("", format_repair=True)]
        messages, manifest = pack_context(pinned, history, "Correct JSON format only", candidate,
                                          budget=policy.input_token_budget, observation_chars=policy.observation_chars,
                                          native=True, reviewing=True, required_review_tools=required_tools)
        assert candidate_message in [m.to_wire() for m in messages]
        assert not manifest["compressed_history"] and not manifest["omitted_history"]
        assert not missing_review_evidence(history, manifest, required_tools, observation_chars=policy.observation_chars)
        for item in history:
            if item["tool"] in required_tools and item.get("ok"):
                assert any(m.content == "[untrusted complete review Observation]\n" + canonical(item) for m in messages)
        config = phase_llm_config(LLMConfig(provider="deepseek", model="archive-configuration-contract"), policy,
                                  phase="reflect", native=True, wire_tools=(), effort_overrides={}, review_format_repair=True)
        wire = provider._request_kwargs(messages, config)
        assert wire["messages"] == [m.to_wire() for m in messages]
        assert wire["extra_body"] == {"thinking": {"type": "disabled"}} and "reasoning_effort" not in wire
        with pytest.raises(ValueError, match="nothing was silently dropped"):
            pack_context(pinned, history, "Correct JSON format only", candidate, budget=4000,
                         observation_chars=policy.observation_chars, native=True, reviewing=True,
                         required_review_tools=required_tools)
    original_review = parse_review(responses[7]["visible"]["text"])
    assert original_review["accept"] is False and len(original_review["issues"]) == 2
    state = _state()
    state.update(candidate=candidate, history=history)
    assert apply_review_decision(state, original_review, format_repair=True, max_reflections=policy.max_reflections) == "revision"
    assert state["review_issues"] == original_review["issues"] and not state["reflection_accepted"]
    assert provider._client is None
    assert all(path.read_bytes() == data for path, data in originals.items())
