"""Arithmetic checks for visible runtime budgets; no provider substitution."""
from dataclasses import asdict

import pytest

from app.harness.agent_loop.executor import budget_message
from app.harness.agent_loop.policy import AgentLoopPolicy


def test_actual_dispatches_and_attempts_reduce_visible_budget() -> None:
    policy = AgentLoopPolicy(max_model_calls=12, max_tool_steps=10, max_validation_repairs=3)
    counts = {"model_requests": 4, "tool_dispatches": 7, "validation_repairs": 1}
    content = budget_message(policy, counts).content
    assert '"model_calls":8' in content
    assert '"tool_calls":3' in content
    assert '"validation_repairs":2' in content
    assert counts == {"model_requests": 4, "tool_dispatches": 7, "validation_repairs": 1}


def test_exhausted_counters_never_offer_negative_resources() -> None:
    counts = {"model_requests": 130, "tool_dispatches": 100, "validation_repairs": 90}
    content = budget_message(AgentLoopPolicy(), counts).content
    assert '"model_calls":0' in content and '"tool_calls":0' in content
    assert '"validation_repairs":0' in content


def test_unlimited_requests_preserve_counters_and_explicit_snapshot() -> None:
    policy = AgentLoopPolicy.from_mapping({"max_model_calls": None})
    counts = {"model_requests": 1000, "tool_dispatches": 100, "validation_repairs": 90}
    before = dict(counts)
    content = budget_message(policy, counts).content
    assert '"model_calls":null' in content and "no model-request count limit" in content
    assert '"tool_calls":0' in content and '"validation_repairs":0' in content
    assert policy.allows_model_calls(1000, 3) and policy.remaining_model_calls(1000) is None
    assert counts == before
    assert policy.fingerprint_data()["max_model_calls"] is None
    assert AgentLoopPolicy.from_mapping(asdict(policy)) == policy
    finite = AgentLoopPolicy(max_model_calls=36)
    assert finite.allows_model_calls(33, 3) and not finite.allows_model_calls(34, 3)
    assert not finite.allows_model_calls(36) and finite.remaining_model_calls(1000) == 0
    assert AgentLoopPolicy().max_model_calls == 36


@pytest.mark.parametrize("value", [True, False, 0, -1, "null", "unlimited", float("inf")])
def test_unlimited_requires_explicit_null(value: object) -> None:
    with pytest.raises(ValueError):
        AgentLoopPolicy.from_mapping({"max_model_calls": value})
