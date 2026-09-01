"""Direction-aware paired statistics for discovery evaluations.

This module is deliberately project agnostic.  The default gate matches the
PIMC residual acceptance policy, while callers may supply a different gate for
other objectives.  Aggregation has no search-state side effects; holdout
results never expose a search-feedback value.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from typing import Literal, Self

from pydantic import Field, model_validator

from app.harness.discovery.models import (
    CandidateEvaluation,
    FidelityLevel,
    FrozenRecord,
    MetricValue,
    ObjectiveDirection,
    ObjectiveSpec,
)


DatasetRole = Literal["search", "train", "validation", "holdout"]


class StatisticalGate(FrozenRecord):
    """Configurable promotion criteria for direction-normalized deltas."""

    minimum_pairs: int = Field(default=3, ge=1)
    minimum_mean_improvement: float = 0.10
    minimum_ci95_lower_exclusive: float = 0.0
    maximum_single_seed_degradation: float = Field(default=0.05, ge=0.0)


DEFAULT_PIMC_RESIDUAL_GATE = StatisticalGate()


class GateReason(FrozenRecord):
    code: str = Field(min_length=1)
    observed: float | None = None
    threshold: float | None = None


class PairedMetricDelta(FrozenRecord):
    seed: int
    candidate_evaluation_id: str = Field(min_length=1)
    baseline_evaluation_id: str = Field(min_length=1)
    candidate_value: float
    baseline_value: float
    improvement_delta: float


class EvaluationAggregate(FrozenRecord):
    schema_id: Literal["evaluation_aggregate.v1"] = "evaluation_aggregate.v1"
    candidate_id: str = Field(min_length=1)
    baseline_candidate_id: str = Field(min_length=1)
    candidate_run_id: str = Field(min_length=1)
    baseline_run_id: str = Field(min_length=1)
    objective_name: str = Field(min_length=1)
    objective_direction: ObjectiveDirection
    unit: str = ""
    fidelity: FidelityLevel
    evaluator_hash: str = Field(min_length=1)
    dataset_hash: str = ""
    dataset_role: DatasetRole = "search"
    pairs: tuple[PairedMetricDelta, ...]
    n: int = Field(ge=1)
    mean_improvement: float
    sample_standard_deviation: float | None = None
    ci95_lower: float | None = None
    ci95_upper: float | None = None
    worst_seed_improvement: float
    gate_passed: bool
    reasons: tuple[GateReason, ...] = ()
    search_feedback_allowed: bool
    search_feedback_value: float | None = None

    @model_validator(mode="after")
    def holdout_cannot_be_search_feedback(self) -> Self:
        if self.dataset_role == "holdout" and (
            self.search_feedback_allowed or self.search_feedback_value is not None
        ):
            raise ValueError("holdout aggregate cannot expose search feedback")
        return self


_PairKey = tuple[int, FidelityLevel, str, str]
_CohortKey = tuple[FidelityLevel, str, str]

# Two-sided 95% Student-t critical values indexed by degrees of freedom.
# Index zero is unused; n=2..32 corresponds to df=1..31.  DiscoveryRunSpec
# allows 32 shared seeds, so the exact small-sample boundary must cover all of
# them rather than switching early to the normal approximation.
_T_CRITICAL_95: tuple[float, ...] = (
    0.0,
    12.706,
    4.303,
    3.182,
    2.776,
    2.571,
    2.447,
    2.365,
    2.306,
    2.262,
    2.228,
    2.201,
    2.179,
    2.160,
    2.145,
    2.131,
    2.120,
    2.110,
    2.101,
    2.093,
    2.086,
    2.080,
    2.074,
    2.069,
    2.064,
    2.060,
    2.056,
    2.052,
    2.048,
    2.045,
    2.042,
    2.040,
)


def aggregate_candidate_vs_baseline(
    *,
    candidate_evaluations: Sequence[CandidateEvaluation],
    baseline_evaluations: Sequence[CandidateEvaluation],
    objective: ObjectiveSpec,
    gate: StatisticalGate = DEFAULT_PIMC_RESIDUAL_GATE,
    dataset_role: DatasetRole = "search",
) -> tuple[EvaluationAggregate, ...]:
    """Build one aggregate per fidelity/evaluator/dataset cohort.

    A pair exists only when seed, fidelity, evaluator hash, and dataset hash
    all match.  Duplicate pairing keys and malformed objective metrics fail
    closed instead of choosing an evaluation arbitrarily.
    """

    candidate_id, candidate_run_id = _evaluation_identity(
        candidate_evaluations,
        label="candidate",
    )
    baseline_id, baseline_run_id = _evaluation_identity(
        baseline_evaluations,
        label="baseline",
    )
    if candidate_id == baseline_id:
        raise ValueError("candidate and baseline IDs must differ")

    candidate_by_key = _index_evaluations(candidate_evaluations, label="candidate")
    baseline_by_key = _index_evaluations(baseline_evaluations, label="baseline")
    common_keys = sorted(
        set(candidate_by_key).intersection(baseline_by_key),
        key=_pair_key_sort_key,
    )
    if not common_keys:
        raise ValueError(
            "candidate and baseline have no evaluations with matching "
            "seed, fidelity, evaluator, and dataset"
        )

    grouped: dict[_CohortKey, list[PairedMetricDelta]] = {}
    unit_by_cohort: dict[_CohortKey, str] = {}
    for key in common_keys:
        candidate = candidate_by_key[key]
        baseline = baseline_by_key[key]
        candidate_metric = _validated_metric(candidate, objective)
        baseline_metric = _validated_metric(baseline, objective)
        unit = _validated_unit(candidate_metric, baseline_metric, objective)
        delta = _improvement_delta(
            candidate_metric.value,
            baseline_metric.value,
            objective.direction,
        )
        cohort = (key[1], key[2], key[3])
        grouped.setdefault(cohort, []).append(
            PairedMetricDelta(
                seed=key[0],
                candidate_evaluation_id=candidate.evaluation_id,
                baseline_evaluation_id=baseline.evaluation_id,
                candidate_value=candidate_metric.value,
                baseline_value=baseline_metric.value,
                improvement_delta=delta,
            )
        )
        unit_by_cohort[cohort] = unit

    aggregates: list[EvaluationAggregate] = []
    for cohort in sorted(grouped, key=_cohort_key_sort_key):
        pairs = tuple(sorted(grouped[cohort], key=lambda item: item.seed))
        improvements = [pair.improvement_delta for pair in pairs]
        mean_improvement = statistics.fmean(improvements)
        standard_deviation, ci95_lower, ci95_upper = _confidence_interval_95(
            improvements
        )
        worst_improvement = min(improvements)
        reasons = _gate_reasons(
            n=len(pairs),
            mean_improvement=mean_improvement,
            ci95_lower=ci95_lower,
            worst_improvement=worst_improvement,
            gate=gate,
        )
        search_feedback_allowed = dataset_role != "holdout"
        aggregates.append(
            EvaluationAggregate(
                candidate_id=candidate_id,
                baseline_candidate_id=baseline_id,
                candidate_run_id=candidate_run_id,
                baseline_run_id=baseline_run_id,
                objective_name=objective.name,
                objective_direction=objective.direction,
                unit=unit_by_cohort[cohort],
                fidelity=cohort[0],
                evaluator_hash=cohort[1],
                dataset_hash=cohort[2],
                dataset_role=dataset_role,
                pairs=pairs,
                n=len(pairs),
                mean_improvement=mean_improvement,
                sample_standard_deviation=standard_deviation,
                ci95_lower=ci95_lower,
                ci95_upper=ci95_upper,
                worst_seed_improvement=worst_improvement,
                gate_passed=not reasons,
                reasons=reasons,
                search_feedback_allowed=search_feedback_allowed,
                search_feedback_value=(
                    mean_improvement if search_feedback_allowed else None
                ),
            )
        )
    return tuple(aggregates)


def _evaluation_identity(
    evaluations: Sequence[CandidateEvaluation],
    *,
    label: str,
) -> tuple[str, str]:
    if not evaluations:
        raise ValueError(f"{label} evaluations are required")
    candidate_ids = {evaluation.candidate_id for evaluation in evaluations}
    if len(candidate_ids) != 1:
        raise ValueError(f"{label} evaluations must contain exactly one candidate ID")
    run_ids = {evaluation.run_id for evaluation in evaluations}
    if len(run_ids) != 1:
        raise ValueError(f"{label} evaluations must contain exactly one run ID")
    return next(iter(candidate_ids)), next(iter(run_ids))


def _index_evaluations(
    evaluations: Sequence[CandidateEvaluation],
    *,
    label: str,
) -> dict[_PairKey, CandidateEvaluation]:
    indexed: dict[_PairKey, CandidateEvaluation] = {}
    for evaluation in evaluations:
        key = (
            evaluation.seed,
            evaluation.fidelity,
            evaluation.evaluator_hash,
            evaluation.dataset_hash,
        )
        if key in indexed:
            raise ValueError(
                f"duplicate {label} evaluation pairing key: "
                f"seed={key[0]}, fidelity={key[1].value}, "
                f"evaluator={key[2]}, dataset={key[3]}"
            )
        indexed[key] = evaluation
    return indexed


def _validated_metric(
    evaluation: CandidateEvaluation,
    objective: ObjectiveSpec,
) -> MetricValue:
    metric = evaluation.canonical_metrics.get(objective.name)
    if metric is None:
        raise ValueError(
            f"evaluation '{evaluation.evaluation_id}' is missing objective "
            f"'{objective.name}'"
        )
    if metric.direction != objective.direction:
        raise ValueError(
            f"evaluation '{evaluation.evaluation_id}' objective direction mismatch"
        )
    if not math.isfinite(metric.value):
        raise ValueError(
            f"evaluation '{evaluation.evaluation_id}' objective is not finite"
        )
    return metric


def _validated_unit(
    candidate: MetricValue,
    baseline: MetricValue,
    objective: ObjectiveSpec,
) -> str:
    if candidate.unit != baseline.unit:
        raise ValueError("candidate and baseline objective units differ")
    if objective.unit and candidate.unit != objective.unit:
        raise ValueError("evaluation objective unit does not match ObjectiveSpec")
    return objective.unit or candidate.unit


def _improvement_delta(
    candidate_value: float,
    baseline_value: float,
    direction: ObjectiveDirection,
) -> float:
    if direction == ObjectiveDirection.MAXIMIZE:
        return candidate_value - baseline_value
    return baseline_value - candidate_value


def _confidence_interval_95(
    values: Sequence[float],
) -> tuple[float | None, float | None, float | None]:
    if len(values) < 2:
        return None, None, None
    standard_deviation = statistics.stdev(values)
    standard_error = standard_deviation / math.sqrt(len(values))
    margin = _t_critical_95(len(values)) * standard_error
    mean_value = statistics.fmean(values)
    return (
        standard_deviation,
        mean_value - margin,
        mean_value + margin,
    )


def t_critical_95(n: int) -> float:
    if n < 2:
        raise ValueError("t critical value requires at least two observations")
    if n <= 32:
        return _T_CRITICAL_95[n - 1]
    return 1.96


def _t_critical_95(n: int) -> float:
    """Backward-compatible private alias for existing callers/tests."""

    return t_critical_95(n)


def _gate_reasons(
    *,
    n: int,
    mean_improvement: float,
    ci95_lower: float | None,
    worst_improvement: float,
    gate: StatisticalGate,
) -> tuple[GateReason, ...]:
    reasons: list[GateReason] = []
    if n < gate.minimum_pairs:
        reasons.append(
            GateReason(
                code="insufficient_pairs",
                observed=float(n),
                threshold=float(gate.minimum_pairs),
            )
        )
    if mean_improvement < gate.minimum_mean_improvement:
        reasons.append(
            GateReason(
                code="mean_improvement_below_threshold",
                observed=mean_improvement,
                threshold=gate.minimum_mean_improvement,
            )
        )
    if ci95_lower is None:
        reasons.append(GateReason(code="confidence_interval_unavailable"))
    elif ci95_lower <= gate.minimum_ci95_lower_exclusive:
        reasons.append(
            GateReason(
                code="confidence_interval_lower_not_above_threshold",
                observed=ci95_lower,
                threshold=gate.minimum_ci95_lower_exclusive,
            )
        )
    minimum_allowed = -gate.maximum_single_seed_degradation
    if worst_improvement < minimum_allowed:
        reasons.append(
            GateReason(
                code="single_seed_degradation_exceeded",
                observed=worst_improvement,
                threshold=minimum_allowed,
            )
        )
    return tuple(reasons)


def _pair_key_sort_key(key: _PairKey) -> tuple[str, str, str, int]:
    return key[1].value, key[2], key[3], key[0]


def _cohort_key_sort_key(key: _CohortKey) -> tuple[str, str, str]:
    return key[0].value, key[1], key[2]
