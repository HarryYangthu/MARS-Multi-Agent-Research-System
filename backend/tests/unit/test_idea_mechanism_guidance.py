"""Analytic counterexamples and author/reviewer contracts, without model substitutes."""
from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from app.agents.base import RunRequest
from app.agents.idea.agent import IdeaAgent


def test_zero_linear_coefficient_can_have_nonzero_loss_gradient() -> None:
    # Counterexample to the actual run-17 claim: a fixed Gaussian basis is linear
    # in its coefficient, so zero prediction alone cannot establish zero gradient.
    basis = math.exp(-0.5)
    target = 1.0
    epsilon = 1e-6
    def loss(weight: float) -> float:
        return (weight * basis - target) ** 2
    derivative = (loss(epsilon) - loss(-epsilon)) / (2 * epsilon)
    assert derivative == pytest.approx(-2 * basis * target)
    assert abs(derivative) > 1.0


def test_coupled_zero_factors_are_a_different_initialization_case() -> None:
    epsilon = 1e-6
    def loss(left: float, right: float) -> float:
        return (left * right - 1.0) ** 2
    assert (loss(epsilon, 0) - loss(-epsilon, 0)) / (2 * epsilon) == 0
    assert (loss(0, epsilon) - loss(0, -epsilon)) / (2 * epsilon) == 0
    assert (loss(epsilon, 1) - loss(-epsilon, 1)) / (2 * epsilon) == pytest.approx(-2)


@pytest.mark.asyncio
async def test_author_and_reviewer_require_specific_transfer_and_initialization_reasoning() -> None:
    root = Path(__file__).resolve().parents[3]
    scenario = yaml.safe_load((root / "configs/evaluation/idea_research_per_insight_real.yaml").read_text())
    agent = IdeaAgent()
    context = await agent.build_context(RunRequest("pimc", "Authored prompt contract", extra={
        "context_sources": {"project_rules": False, "code_repositories": False},
        "idea_requirements": scenario["requirements"]}))
    assert "zero output or zero coefficients alone do not imply zero gradients" in context.task
    assert "what is changed and why the changed structure is a plausible hypothesis" in context.task
    assert "paper-specific link" in context.task
    rubric = agent.reflection_rubric()
    assert "evaluate the Jacobian and loss gradient" in rubric
    assert "Follow the actual operations" in rubric
    assert "Do not require a novel hypothesis to have already been" in rubric
