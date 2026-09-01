from __future__ import annotations

import math

import pytest

from app.harness.discovery.evaluation_aggregate import (
    EvaluationAggregate,
    StatisticalGate,
    _t_critical_95,
    aggregate_candidate_vs_baseline,
)
from app.harness.discovery.models import (
    CandidateEvaluation,
    FidelityLevel,
    MetricValue,
    ObjectiveDirection,
    ObjectiveSpec,
)


def _evaluation(
    candidate_id: str,
    *,
    seed: int,
    value: float,
    direction: ObjectiveDirection = ObjectiveDirection.MINIMIZE,
    fidelity: FidelityLevel = FidelityLevel.F2,
    evaluator_hash: str = "eval-hash",
    dataset_hash: str = "dataset-hash",
    metric_name: str = "RES",
    unit: str = "dB",
    run_id: str = "run-1",
) -> CandidateEvaluation:
    return CandidateEvaluation(
        evaluation_id=(
            f"eval-{candidate_id}-{seed}-{fidelity.value}-"
            f"{evaluator_hash}-{dataset_hash}"
        ),
        candidate_id=candidate_id,
        run_id=run_id,
        fidelity=fidelity,
        seed=seed,
        evaluator_hash=evaluator_hash,
        dataset_hash=dataset_hash,
        canonical_metrics={
            metric_name: MetricValue(
                value=value,
                unit=unit,
                direction=direction,
            )
        },
        hard_constraints_passed=True,
    )


def _objective(
    direction: ObjectiveDirection = ObjectiveDirection.MINIMIZE,
) -> ObjectiveSpec:
    return ObjectiveSpec(name="RES", direction=direction, unit="dB")


def test_minimize_objective_uses_direction_normalized_paired_improvement() -> None:
    baseline = [
        _evaluation("baseline", seed=seed, value=10.0)
        for seed in (1, 2, 3)
    ]
    candidate = [
        _evaluation("candidate", seed=1, value=9.80),
        _evaluation("candidate", seed=2, value=9.85),
        _evaluation("candidate", seed=3, value=9.90),
    ]

    (aggregate,) = aggregate_candidate_vs_baseline(
        candidate_evaluations=candidate,
        baseline_evaluations=baseline,
        objective=_objective(),
    )

    assert aggregate.schema_id == "evaluation_aggregate.v1"
    assert aggregate.n == 3
    assert [pair.seed for pair in aggregate.pairs] == [1, 2, 3]
    assert [pair.improvement_delta for pair in aggregate.pairs] == pytest.approx(
        [0.20, 0.15, 0.10]
    )
    assert aggregate.mean_improvement == pytest.approx(0.15)
    assert aggregate.sample_standard_deviation == pytest.approx(0.05)
    assert aggregate.ci95_lower == pytest.approx(0.025783, abs=1e-6)
    assert aggregate.ci95_upper == pytest.approx(0.274217, abs=1e-6)
    assert aggregate.gate_passed is True
    assert aggregate.reasons == ()
    assert aggregate.search_feedback_value == pytest.approx(0.15)


def test_maximize_objective_uses_candidate_minus_baseline() -> None:
    baseline = [
        _evaluation(
            "baseline",
            seed=seed,
            value=float(seed),
            direction=ObjectiveDirection.MAXIMIZE,
        )
        for seed in (1, 2, 3)
    ]
    candidate = [
        _evaluation(
            "candidate",
            seed=seed,
            value=float(seed) + 0.2,
            direction=ObjectiveDirection.MAXIMIZE,
        )
        for seed in (1, 2, 3)
    ]

    (aggregate,) = aggregate_candidate_vs_baseline(
        candidate_evaluations=candidate,
        baseline_evaluations=baseline,
        objective=_objective(ObjectiveDirection.MAXIMIZE),
    )

    assert aggregate.mean_improvement == pytest.approx(0.2)
    assert aggregate.ci95_lower == pytest.approx(0.2)
    assert aggregate.gate_passed is True


def test_pairing_requires_matching_seed_fidelity_evaluator_and_dataset() -> None:
    candidate = [
        _evaluation("candidate", seed=1, value=9.8),
        _evaluation("candidate", seed=2, value=9.8),
        _evaluation(
            "candidate",
            seed=4,
            value=9.7,
            fidelity=FidelityLevel.F3,
            dataset_hash="holdout-hash",
        ),
    ]
    baseline = [
        _evaluation("baseline", seed=2, value=10.0),
        _evaluation("baseline", seed=3, value=10.0),
        _evaluation(
            "baseline",
            seed=4,
            value=10.0,
            fidelity=FidelityLevel.F3,
            dataset_hash="holdout-hash",
        ),
    ]

    aggregates = aggregate_candidate_vs_baseline(
        candidate_evaluations=candidate,
        baseline_evaluations=baseline,
        objective=_objective(),
    )

    assert [(item.fidelity, item.n) for item in aggregates] == [
        (FidelityLevel.F2, 1),
        (FidelityLevel.F3, 1),
    ]
    assert [item.pairs[0].seed for item in aggregates] == [2, 4]


def test_default_gate_records_machine_readable_failure_reasons() -> None:
    baseline = [
        _evaluation("baseline", seed=seed, value=0.0)
        for seed in (1, 2, 3)
    ]
    candidate = [
        _evaluation("candidate", seed=1, value=-0.2),
        _evaluation("candidate", seed=2, value=-0.2),
        _evaluation("candidate", seed=3, value=0.06),
    ]

    (aggregate,) = aggregate_candidate_vs_baseline(
        candidate_evaluations=candidate,
        baseline_evaluations=baseline,
        objective=_objective(),
    )

    assert aggregate.mean_improvement > 0.10
    assert aggregate.gate_passed is False
    assert [reason.code for reason in aggregate.reasons] == [
        "confidence_interval_lower_not_above_threshold",
        "single_seed_degradation_exceeded",
    ]


def test_gate_reports_insufficient_pairs_and_unavailable_interval() -> None:
    (aggregate,) = aggregate_candidate_vs_baseline(
        candidate_evaluations=[_evaluation("candidate", seed=1, value=9.8)],
        baseline_evaluations=[_evaluation("baseline", seed=1, value=10.0)],
        objective=_objective(),
    )

    assert aggregate.sample_standard_deviation is None
    assert aggregate.ci95_lower is None
    assert [reason.code for reason in aggregate.reasons] == [
        "insufficient_pairs",
        "confidence_interval_unavailable",
    ]


def test_statistical_gate_is_configurable() -> None:
    baseline = [
        _evaluation("baseline", seed=seed, value=10.0)
        for seed in (1, 2)
    ]
    candidate = [
        _evaluation("candidate", seed=seed, value=9.95)
        for seed in (1, 2)
    ]

    (aggregate,) = aggregate_candidate_vs_baseline(
        candidate_evaluations=candidate,
        baseline_evaluations=baseline,
        objective=_objective(),
        gate=StatisticalGate(
            minimum_pairs=2,
            minimum_mean_improvement=0.04,
            minimum_ci95_lower_exclusive=0.0,
            maximum_single_seed_degradation=0.0,
        ),
    )

    assert aggregate.gate_passed is True


def test_holdout_aggregate_never_exposes_search_feedback() -> None:
    baseline = [
        _evaluation("baseline", seed=seed, value=10.0)
        for seed in (1, 2, 3)
    ]
    candidate = [
        _evaluation("candidate", seed=seed, value=9.8)
        for seed in (1, 2, 3)
    ]

    (aggregate,) = aggregate_candidate_vs_baseline(
        candidate_evaluations=candidate,
        baseline_evaluations=baseline,
        objective=_objective(),
        dataset_role="holdout",
    )

    assert aggregate.gate_passed is True
    assert aggregate.dataset_role == "holdout"
    assert aggregate.search_feedback_allowed is False
    assert aggregate.search_feedback_value is None

    invalid_payload = aggregate.model_dump()
    invalid_payload["search_feedback_allowed"] = True
    invalid_payload["search_feedback_value"] = aggregate.mean_improvement
    with pytest.raises(ValueError, match="holdout aggregate"):
        EvaluationAggregate.model_validate(invalid_payload)


def test_no_matching_pair_fails_closed() -> None:
    with pytest.raises(ValueError, match="no evaluations with matching"):
        aggregate_candidate_vs_baseline(
            candidate_evaluations=[_evaluation("candidate", seed=1, value=9.8)],
            baseline_evaluations=[_evaluation("baseline", seed=2, value=10.0)],
            objective=_objective(),
        )


def test_duplicate_pairing_key_fails_closed() -> None:
    duplicate = _evaluation("candidate", seed=1, value=9.8)
    with pytest.raises(ValueError, match="duplicate candidate evaluation"):
        aggregate_candidate_vs_baseline(
            candidate_evaluations=[duplicate, duplicate.model_copy()],
            baseline_evaluations=[_evaluation("baseline", seed=1, value=10.0)],
            objective=_objective(),
        )


@pytest.mark.parametrize(
    "candidate_evaluation",
    [
        _evaluation(
            "candidate",
            seed=1,
            value=9.8,
            direction=ObjectiveDirection.MAXIMIZE,
        ),
        _evaluation("candidate", seed=1, value=math.nan),
        _evaluation("candidate", seed=1, value=9.8, unit="linear"),
    ],
)
def test_invalid_objective_metric_fails_closed(
    candidate_evaluation: CandidateEvaluation,
) -> None:
    with pytest.raises(ValueError):
        aggregate_candidate_vs_baseline(
            candidate_evaluations=[candidate_evaluation],
            baseline_evaluations=[_evaluation("baseline", seed=1, value=10.0)],
            objective=_objective(),
        )


def test_t_critical_table_and_large_sample_fallback() -> None:
    assert _t_critical_95(2) == pytest.approx(12.706)
    assert _t_critical_95(30) == pytest.approx(2.045)
    assert _t_critical_95(31) == pytest.approx(2.042)
    assert _t_critical_95(32) == pytest.approx(2.040)
    assert _t_critical_95(33) == pytest.approx(1.96)
    with pytest.raises(ValueError, match="at least two"):
        _t_critical_95(1)


def test_thirty_one_seed_boundary_does_not_use_normal_approximation_early() -> None:
    mean_improvement = 0.10
    deviation = 0.05 * math.sqrt(31)
    improvements = (
        [mean_improvement - deviation] * 15
        + [mean_improvement]
        + [mean_improvement + deviation] * 15
    )
    baseline = [
        _evaluation("baseline", seed=seed, value=10.0)
        for seed in range(31)
    ]
    candidate = [
        _evaluation(
            "candidate",
            seed=seed,
            value=10.0 - improvement,
        )
        for seed, improvement in enumerate(improvements)
    ]

    (aggregate,) = aggregate_candidate_vs_baseline(
        candidate_evaluations=candidate,
        baseline_evaluations=baseline,
        objective=_objective(),
        gate=StatisticalGate(
            minimum_pairs=31,
            minimum_mean_improvement=0.0,
            minimum_ci95_lower_exclusive=0.0,
            maximum_single_seed_degradation=1.0,
        ),
    )

    assert aggregate.ci95_lower == pytest.approx(-0.0021, abs=1e-4)
    assert aggregate.gate_passed is False
    assert "confidence_interval_lower_not_above_threshold" in {
        reason.code for reason in aggregate.reasons
    }
