"""Direction-aware, fidelity-safe candidate promotion decisions."""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.harness.discovery.archive import evaluation_errors
from app.harness.discovery.models import (
    CandidateEvaluation,
    FidelityLevel,
    ObjectiveDirection,
    ObjectiveSpec,
)

_NEXT_FIDELITY: dict[FidelityLevel, FidelityLevel] = {
    FidelityLevel.F0: FidelityLevel.F1,
    FidelityLevel.F1: FidelityLevel.F2,
    FidelityLevel.F2: FidelityLevel.F3,
    FidelityLevel.F3: FidelityLevel.F4,
}


@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    current_fidelity: FidelityLevel
    next_fidelity: FidelityLevel | None
    reasons: tuple[str, ...] = ()


def decide_promotion(
    evaluation: CandidateEvaluation,
    objectives: tuple[ObjectiveSpec, ...],
    *,
    thresholds: dict[str, float] | None = None,
) -> PromotionDecision:
    errors = list(evaluation_errors(evaluation, objectives))
    resolved_thresholds = dict(thresholds or {})
    for objective in objectives:
        threshold = resolved_thresholds.get(objective.name, objective.hard_constraint)
        if threshold is None:
            continue
        if not math.isfinite(threshold):
            errors.append(f"threshold for '{objective.name}' is not finite")
            continue
        metric = evaluation.canonical_metrics.get(objective.name)
        if metric is None or not math.isfinite(metric.value):
            continue
        passed = (
            metric.value <= threshold
            if objective.direction == ObjectiveDirection.MINIMIZE
            else metric.value >= threshold
        )
        if not passed:
            errors.append(f"objective '{objective.name}' missed promotion threshold")
    next_fidelity = _NEXT_FIDELITY.get(evaluation.fidelity)
    if next_fidelity is None:
        errors.append("candidate is already at the highest fidelity")
    return PromotionDecision(
        promote=not errors,
        current_fidelity=evaluation.fidelity,
        next_fidelity=next_fidelity if not errors else None,
        reasons=tuple(errors),
    )
