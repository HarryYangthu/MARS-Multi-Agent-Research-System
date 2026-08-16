"""Pure budget, patience, manual, and safety stopping policy."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.harness.discovery.models import BudgetLimits


class StopReason(str, Enum):
    CONTINUE = "continue"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PATIENCE_EXHAUSTED = "patience_exhausted"
    MAX_ITERATIONS = "max_iterations"
    SAFETY_VIOLATION = "safety_violation"
    MANUAL = "manual"


@dataclass(frozen=True)
class BudgetUsage:
    proposals: int = 0
    llm_tokens: int = 0
    gpu_seconds: float = 0.0
    wall_seconds: float = 0.0
    api_cost: float = 0.0

    def __post_init__(self) -> None:
        if (
            self.proposals < 0
            or self.llm_tokens < 0
            or self.gpu_seconds < 0.0
            or self.wall_seconds < 0.0
            or self.api_cost < 0.0
        ):
            raise ValueError("budget usage must be non-negative")


@dataclass(frozen=True)
class PatienceState:
    valid_candidates: int = 0
    since_last_improvement: int = 0

    def __post_init__(self) -> None:
        if self.valid_candidates < 0 or self.since_last_improvement < 0:
            raise ValueError("patience counters must be non-negative")


@dataclass(frozen=True)
class StopPolicy:
    max_without_improvement: int = 10
    min_valid_candidates: int = 1
    max_iterations: int | None = None

    def __post_init__(self) -> None:
        if self.max_without_improvement < 0 or self.min_valid_candidates < 0:
            raise ValueError("patience policy values must be non-negative")
        if self.max_iterations is not None and self.max_iterations < 0:
            raise ValueError("max_iterations must be non-negative")


@dataclass(frozen=True)
class StopDecision:
    should_stop: bool
    reason: StopReason
    details: tuple[str, ...] = ()


def update_patience(state: PatienceState, *, improved: bool) -> PatienceState:
    return PatienceState(
        valid_candidates=state.valid_candidates + 1,
        since_last_improvement=0 if improved else state.since_last_improvement + 1,
    )


def evaluate_stop(
    *,
    limits: BudgetLimits,
    usage: BudgetUsage,
    patience: PatienceState,
    policy: StopPolicy | None = None,
    iteration: int = 0,
    safety_violations: tuple[str, ...] = (),
    manual_stop: bool = False,
) -> StopDecision:
    cfg = policy or StopPolicy()
    if manual_stop:
        return StopDecision(True, StopReason.MANUAL, ("manual stop requested",))
    if safety_violations:
        return StopDecision(True, StopReason.SAFETY_VIOLATION, tuple(sorted(safety_violations)))
    exhausted = _exhausted_budget(limits, usage)
    if exhausted:
        return StopDecision(True, StopReason.BUDGET_EXHAUSTED, exhausted)
    if cfg.max_iterations is not None and iteration >= cfg.max_iterations:
        return StopDecision(True, StopReason.MAX_ITERATIONS, (f"iteration={iteration}",))
    if (
        cfg.max_without_improvement > 0
        and patience.valid_candidates >= cfg.min_valid_candidates
        and patience.since_last_improvement >= cfg.max_without_improvement
    ):
        return StopDecision(
            True,
            StopReason.PATIENCE_EXHAUSTED,
            (f"without_improvement={patience.since_last_improvement}",),
        )
    return StopDecision(False, StopReason.CONTINUE)


def _exhausted_budget(limits: BudgetLimits, usage: BudgetUsage) -> tuple[str, ...]:
    exhausted: list[str] = []
    if usage.proposals >= limits.proposals:
        exhausted.append("proposals")
    if limits.llm_tokens > 0 and usage.llm_tokens >= limits.llm_tokens:
        exhausted.append("llm_tokens")
    if limits.gpu_seconds > 0.0 and usage.gpu_seconds >= limits.gpu_seconds:
        exhausted.append("gpu_seconds")
    if usage.wall_seconds >= limits.wall_seconds:
        exhausted.append("wall_seconds")
    if limits.api_cost > 0.0 and usage.api_cost >= limits.api_cost:
        exhausted.append("api_cost")
    return tuple(exhausted)
