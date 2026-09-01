from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.harness.discovery.candidate_builder import build_candidate_record
from app.harness.discovery.models import (
    CandidateEvaluation,
    CandidateRecord,
    FidelityLevel,
    MetricValue,
    ModelGenome,
    ObjectiveDirection,
    ObjectiveSpec,
    ResearchTaskContract,
)
from app.storage.discovery_search_state_store import DiscoverySearchStateStore


def test_search_state_rebuilds_real_rewards_and_offspring_idempotently(
    tmp_path: Path,
) -> None:
    contract = ResearchTaskContract(
        run_id="adaptive-run",
        project="demo",
        objective="minimize dev loss",
        objectives=(
            ObjectiveSpec(
                name="dev_loss",
                direction=ObjectiveDirection.MINIMIZE,
            ),
        ),
    )
    first = _candidate(
        run_id=contract.run_id,
        value=0,
        model="model_a",
        operator="mutate",
    )
    second = _candidate(
        run_id=contract.run_id,
        value=1,
        model="model_b",
        operator="crossover",
    )
    child = _candidate(
        run_id=contract.run_id,
        value=2,
        model="model_b",
        operator="crossover",
        parent_ids=(second.candidate_id,),
        iteration=1,
    )
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    evaluations = (
        _evaluation(first.candidate_id, 10.0, started, contract.run_id, index=0),
        _evaluation(second.candidate_id, 5.0, started, contract.run_id, index=1),
        _evaluation(child.candidate_id, 4.0, started, contract.run_id, index=2),
    )
    store = DiscoverySearchStateStore(tmp_path, run_id=contract.run_id)

    state = store.rebuild(
        contract=contract,
        candidates=(first, second, child),
        evaluations=evaluations,
    )
    repeated = store.rebuild(
        contract=contract,
        candidates=(first, second, child),
        evaluations=evaluations,
    )

    assert state.best_candidate_id == child.candidate_id
    assert state.best_quality == -4.0
    assert state.valid_candidates == 3
    assert state.since_last_improvement == 0
    assert {item.arm_id: (item.pulls, item.total_reward) for item in state.model_arms} == {
        "model_a": (1, 0.0),
        "model_b": (2, 6.0),
    }
    assert {
        item.arm_id: (item.pulls, item.total_reward) for item in state.operator_arms
    } == {
        "crossover": (2, 6.0),
        "mutate": (1, 0.0),
    }
    parent = next(
        item for item in state.candidates if item.candidate_id == second.candidate_id
    )
    assert parent.quality == -5.0
    assert parent.offspring_count == 1
    assert repeated.model_arms == state.model_arms
    assert repeated.operator_arms == state.operator_arms
    assert repeated.observed_evaluation_ids == state.observed_evaluation_ids
    assert store.load() == repeated


def test_shared_seed_evidence_counts_as_one_candidate_and_one_bandit_pull(
    tmp_path: Path,
) -> None:
    contract = ResearchTaskContract(
        run_id="shared-seed-run",
        project="demo",
        objective="minimize dev loss",
        objectives=(
            ObjectiveSpec(
                name="dev_loss",
                direction=ObjectiveDirection.MINIMIZE,
            ),
        ),
        stop_policy={"max_without_improvement": 1, "min_valid_candidates": 1},
    )
    candidate = _candidate(
        run_id=contract.run_id,
        value=0,
        model="model_a",
        operator="mutate",
    )
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    evaluations = tuple(
        _evaluation(
            candidate.candidate_id,
            value,
            started,
            contract.run_id,
            index=index,
        )
        for index, value in enumerate((10.0, 11.0, 12.0), start=1)
    )

    state = DiscoverySearchStateStore(
        tmp_path,
        run_id=contract.run_id,
    ).rebuild(
        contract=contract,
        candidates=(candidate,),
        evaluations=evaluations,
    )

    assert state.valid_candidates == 1
    assert state.since_last_improvement == 0
    assert state.model_arms[0].pulls == 1
    assert state.operator_arms[0].pulls == 1
    assert state.candidates[0].evaluation_count == 3
    assert state.candidates[0].primary_value == 11.0


def _candidate(
    *,
    run_id: str,
    value: int,
    model: str,
    operator: str,
    parent_ids: tuple[str, ...] = (),
    iteration: int = 0,
) -> CandidateRecord:
    return build_candidate_record(
        run_id=run_id,
        genome=ModelGenome(
            family="demo",
            hyperparameters={"value": value},
            mutable_zones=("hyperparameters.value",),
        ),
        creator="test",
        model_name=model,
        operator=operator,
        parent_ids=parent_ids,
        generation=iteration,
        iteration=iteration,
    )


def _evaluation(
    candidate_id: str,
    value: float,
    started: datetime,
    run_id: str,
    *,
    index: int,
) -> CandidateEvaluation:
    return CandidateEvaluation(
        evaluation_id=f"evaluation-{index}",
        candidate_id=candidate_id,
        run_id=run_id,
        fidelity=FidelityLevel.F0,
        seed=index,
        evaluator_hash="sha256:evaluator",
        canonical_metrics={
            "dev_loss": MetricValue(
                value=value,
                direction=ObjectiveDirection.MINIMIZE,
            ),
        },
        hard_constraints_passed=True,
        created_at=started + timedelta(seconds=index),
    )
