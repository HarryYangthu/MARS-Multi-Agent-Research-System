from __future__ import annotations

import math

from app.harness.discovery.archive import MapElitesArchive, ParetoArchive
from app.harness.discovery.models import (
    CandidateEvaluation,
    FidelityLevel,
    MetricValue,
    ObjectiveDirection,
    ObjectiveSpec,
)

OBJECTIVES = (
    ObjectiveSpec(name="quality", direction=ObjectiveDirection.MAXIMIZE),
    ObjectiveSpec(name="cost", direction=ObjectiveDirection.MINIMIZE),
)


def _evaluation(
    candidate_id: str,
    *,
    quality: float | None,
    cost: float | None,
    constraints: bool = True,
) -> CandidateEvaluation:
    metrics: dict[str, MetricValue] = {}
    if quality is not None:
        metrics["quality"] = MetricValue(
            value=quality,
            direction=ObjectiveDirection.MAXIMIZE,
        )
    if cost is not None:
        metrics["cost"] = MetricValue(
            value=cost,
            direction=ObjectiveDirection.MINIMIZE,
        )
    return CandidateEvaluation(
        evaluation_id=f"eval-{candidate_id}",
        candidate_id=candidate_id,
        run_id="run-1",
        fidelity=FidelityLevel.F1,
        seed=7,
        evaluator_hash="sha256:evaluator",
        canonical_metrics=metrics,
        hard_constraints_passed=constraints,
    )


def test_pareto_front_is_correct_and_insertion_order_independent() -> None:
    rows = (
        _evaluation("candidate-a", quality=0.9, cost=10.0),
        _evaluation("candidate-b", quality=0.8, cost=8.0),
        _evaluation("candidate-c", quality=0.7, cost=11.0),
        _evaluation("candidate-d", quality=0.95, cost=12.0),
    )
    forward = ParetoArchive(OBJECTIVES)
    reverse = ParetoArchive(OBJECTIVES)
    for row in rows:
        forward.add(row)
    for row in reversed(rows):
        reverse.add(row)

    assert forward.candidate_ids() == ("candidate-a", "candidate-b", "candidate-d")
    assert reverse.candidate_ids() == forward.candidate_ids()
    assert forward.snapshot(run_id="run-1", iteration=1).snapshot_hash == reverse.snapshot(
        run_id="run-1", iteration=1
    ).snapshot_hash


def test_missing_non_finite_and_blocked_metrics_never_enter_pareto() -> None:
    archive = ParetoArchive(OBJECTIVES)
    rows = (
        _evaluation("missing", quality=0.5, cost=None),
        _evaluation("nan", quality=math.nan, cost=1.0),
        _evaluation("inf", quality=0.5, cost=math.inf),
        _evaluation("blocked", quality=1.0, cost=0.0, constraints=False),
    )

    updates = [archive.add(row) for row in rows]

    assert not archive.candidate_ids()
    assert all(not update.accepted and update.reasons for update in updates)


def test_map_elites_replacement_and_ties_are_deterministic() -> None:
    first = MapElitesArchive(maximize=True)
    second = MapElitesArchive(maximize=True)
    for candidate_id, quality in (("candidate-z", 1.0), ("candidate-a", 1.0), ("candidate-b", 0.5)):
        first.add(niche="niche-1", candidate_id=candidate_id, quality=quality)
    for candidate_id, quality in (("candidate-b", 0.5), ("candidate-a", 1.0), ("candidate-z", 1.0)):
        second.add(niche="niche-1", candidate_id=candidate_id, quality=quality)

    assert first.candidate_ids() == {"niche-1": "candidate-a"}
    assert second.candidate_ids() == first.candidate_ids()
    assert not first.add(niche="niche-2", candidate_id="invalid", quality=math.nan)
