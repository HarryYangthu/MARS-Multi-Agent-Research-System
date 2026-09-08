"""Arithmetic checks for visible runtime budgets; no provider substitution."""
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
