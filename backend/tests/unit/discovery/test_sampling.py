from __future__ import annotations

import pytest

from app.harness.discovery.sampling import (
    BanditArm,
    ParentCandidate,
    ParentSamplingConfig,
    sample_parent,
    select_shinka,
    select_ucb,
    update_arm,
)


PARENTS = (
    ParentCandidate("candidate-c", quality=0.2, scarcity=1.0, uncertainty=0.8),
    ParentCandidate("candidate-a", quality=1.0, scarcity=0.1, uncertainty=0.1, offspring_count=4),
    ParentCandidate("candidate-b", quality=0.6, scarcity=0.6, uncertainty=0.5),
)


def test_weighted_parent_selection_is_seeded_and_fully_audited() -> None:
    config = ParentSamplingConfig(exploration_rate=0.2)
    first = sample_parent(PARENTS, seed=42, config=config)
    second = sample_parent(tuple(reversed(PARENTS)), seed=42, config=config)

    assert first == second
    assert first.selected_id in {parent.candidate_id for parent in PARENTS}
    assert tuple(choice.candidate_id for choice in first.choices) == (
        "candidate-a",
        "candidate-b",
        "candidate-c",
    )
    assert sum(choice.probability for choice in first.choices) == pytest.approx(1.0)
    assert all(choice.probability > 0.0 for choice in first.choices)


def test_ucb_prioritizes_untried_arm_then_uses_recorded_scores() -> None:
    arms = (
        BanditArm("model-b", pulls=3, total_reward=1.5),
        BanditArm("model-a"),
    )
    first = select_ucb(arms, seed=9)
    second = select_ucb(tuple(reversed(arms)), seed=9)

    assert first == second
    assert first.selected_id == "model-a"
    assert sum(choice.selected for choice in first.choices) == 1
    assert next(choice for choice in first.choices if choice.arm_id == "model-a").score is None

    updated = update_arm(BanditArm("model-a"), reward=0.75)
    assert updated.pulls == 1
    assert updated.mean_reward == pytest.approx(0.75)


def test_shinka_selection_records_parent_model_and_operator_seeds() -> None:
    selection = select_shinka(
        parents=PARENTS,
        models=(BanditArm("model-a"), BanditArm("model-b")),
        operators=(BanditArm("combine"), BanditArm("mutate")),
        seed=101,
    )

    assert selection.parent.seed == 101
    assert selection.model.seed == 102
    assert selection.operator.seed == 103
    assert selection.model.selected_id in {"model-a", "model-b"}
    assert selection.operator.selected_id in {"combine", "mutate"}
