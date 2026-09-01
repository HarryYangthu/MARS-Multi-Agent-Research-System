"""Auditable Shinka-style parent, model, and operator selection."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class ParentCandidate:
    candidate_id: str
    quality: float = 0.0
    scarcity: float = 0.0
    uncertainty: float = 0.0
    recency: float = 0.0
    offspring_count: int = 0


@dataclass(frozen=True)
class ParentSamplingConfig:
    quality_weight: float = 1.0
    scarcity_weight: float = 0.5
    uncertainty_weight: float = 0.25
    recency_weight: float = 0.1
    offspring_penalty: float = 0.5
    exploration_rate: float = 0.1
    temperature: float = 1.0


@dataclass(frozen=True)
class WeightedChoice:
    candidate_id: str
    score: float
    probability: float


@dataclass(frozen=True)
class WeightedSelectionAudit:
    seed: int
    selected_id: str
    choices: tuple[WeightedChoice, ...]
    reason: str


@dataclass(frozen=True)
class BanditArm:
    arm_id: str
    pulls: int = 0
    total_reward: float = 0.0

    @property
    def mean_reward(self) -> float:
        return self.total_reward / self.pulls if self.pulls else 0.0


@dataclass(frozen=True)
class UCBChoice:
    arm_id: str
    pulls: int
    mean_reward: float
    score: float | None
    selected: bool


@dataclass(frozen=True)
class UCBSelectionAudit:
    seed: int
    selected_id: str
    choices: tuple[UCBChoice, ...]
    reason: str


@dataclass(frozen=True)
class ShinkaSelection:
    parent: WeightedSelectionAudit
    model: UCBSelectionAudit
    operator: UCBSelectionAudit


def sample_parent(
    candidates: tuple[ParentCandidate, ...],
    *,
    seed: int,
    config: ParentSamplingConfig | None = None,
) -> WeightedSelectionAudit:
    if not candidates:
        raise ValueError("at least one parent candidate is required")
    cfg = config or ParentSamplingConfig()
    if not 0.0 <= cfg.exploration_rate <= 1.0:
        raise ValueError("exploration_rate must be in [0, 1]")
    if cfg.temperature <= 0.0:
        raise ValueError("temperature must be positive")
    ordered = tuple(sorted(candidates, key=lambda item: item.candidate_id))
    if len({item.candidate_id for item in ordered}) != len(ordered):
        raise ValueError("parent candidate ids must be unique")
    if any(item.offspring_count < 0 for item in ordered):
        raise ValueError("offspring_count must be non-negative")

    quality = _normalize(tuple(item.quality for item in ordered))
    scarcity = _normalize(tuple(item.scarcity for item in ordered))
    uncertainty = _normalize(tuple(item.uncertainty for item in ordered))
    recency = _normalize(tuple(item.recency for item in ordered))
    scores = tuple(
        cfg.quality_weight * quality[index]
        + cfg.scarcity_weight * scarcity[index]
        + cfg.uncertainty_weight * uncertainty[index]
        + cfg.recency_weight * recency[index]
        - cfg.offspring_penalty * math.log1p(item.offspring_count)
        for index, item in enumerate(ordered)
    )
    probabilities = _softmax_mixed(
        scores,
        temperature=cfg.temperature,
        exploration_rate=cfg.exploration_rate,
    )
    randomizer = random.Random(seed)
    selected_index = randomizer.choices(range(len(ordered)), weights=probabilities, k=1)[0]
    choices = tuple(
        WeightedChoice(item.candidate_id, scores[index], probabilities[index])
        for index, item in enumerate(ordered)
    )
    return WeightedSelectionAudit(
        seed=seed,
        selected_id=ordered[selected_index].candidate_id,
        choices=choices,
        reason="weighted quality/scarcity/uncertainty/recency with exploration",
    )


def select_ucb(
    arms: tuple[BanditArm, ...],
    *,
    seed: int,
    exploration: float = math.sqrt(2.0),
) -> UCBSelectionAudit:
    if not arms:
        raise ValueError("at least one bandit arm is required")
    if exploration < 0.0:
        raise ValueError("exploration must be non-negative")
    ordered = tuple(sorted(arms, key=lambda arm: arm.arm_id))
    if len({arm.arm_id for arm in ordered}) != len(ordered):
        raise ValueError("bandit arm ids must be unique")
    if any(arm.pulls < 0 for arm in ordered):
        raise ValueError("bandit pulls must be non-negative")

    untried = [arm for arm in ordered if arm.pulls == 0]
    randomizer = random.Random(seed)
    scores: dict[str, float | None] = {}
    if untried:
        selected = randomizer.choice(untried).arm_id
        total_pulls = max(1, sum(arm.pulls for arm in ordered))
        for arm in ordered:
            scores[arm.arm_id] = (
                None
                if arm.pulls == 0
                else arm.mean_reward
                + exploration * math.sqrt(math.log(total_pulls + 1) / arm.pulls)
            )
        reason = "selected an untried arm before exploiting observed rewards"
    else:
        total_pulls = sum(arm.pulls for arm in ordered)
        numeric_scores = {
            arm.arm_id: arm.mean_reward
            + exploration * math.sqrt(math.log(total_pulls + 1) / arm.pulls)
            for arm in ordered
        }
        best_score = max(numeric_scores.values())
        tied = sorted(
            arm_id
            for arm_id, score in numeric_scores.items()
            if math.isclose(score, best_score, rel_tol=1e-12, abs_tol=1e-12)
        )
        selected = randomizer.choice(tied)
        scores.update(numeric_scores)
        reason = "selected maximum upper-confidence-bound score"
    choices = tuple(
        UCBChoice(
            arm_id=arm.arm_id,
            pulls=arm.pulls,
            mean_reward=arm.mean_reward,
            score=scores[arm.arm_id],
            selected=arm.arm_id == selected,
        )
        for arm in ordered
    )
    return UCBSelectionAudit(seed=seed, selected_id=selected, choices=choices, reason=reason)


def update_arm(arm: BanditArm, *, reward: float) -> BanditArm:
    if not math.isfinite(reward):
        raise ValueError("reward must be finite")
    return BanditArm(
        arm_id=arm.arm_id,
        pulls=arm.pulls + 1,
        total_reward=arm.total_reward + reward,
    )


def select_shinka(
    *,
    parents: tuple[ParentCandidate, ...],
    models: tuple[BanditArm, ...],
    operators: tuple[BanditArm, ...],
    seed: int,
    parent_config: ParentSamplingConfig | None = None,
    exploration: float = math.sqrt(2.0),
) -> ShinkaSelection:
    return ShinkaSelection(
        parent=sample_parent(parents, seed=seed, config=parent_config),
        model=select_ucb(models, seed=seed + 1, exploration=exploration),
        operator=select_ucb(operators, seed=seed + 2, exploration=exploration),
    )


def _normalize(values: tuple[float, ...]) -> tuple[float, ...]:
    if any(not math.isfinite(value) for value in values):
        raise ValueError("sampling features must be finite")
    low = min(values)
    high = max(values)
    if math.isclose(low, high):
        return tuple(0.5 for _ in values)
    return tuple((value - low) / (high - low) for value in values)


def _softmax_mixed(
    scores: tuple[float, ...],
    *,
    temperature: float,
    exploration_rate: float,
) -> tuple[float, ...]:
    maximum = max(scores)
    exponentials = tuple(math.exp((score - maximum) / temperature) for score in scores)
    total = sum(exponentials)
    uniform = 1.0 / len(scores)
    return tuple(
        (1.0 - exploration_rate) * (value / total) + exploration_rate * uniform
        for value in exponentials
    )
