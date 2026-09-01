from __future__ import annotations

from app.harness.discovery.candidate_builder import ConfigDelta, DeltaOperation, build_candidate_record
from app.harness.discovery.models import (
    BudgetLimits,
    CandidateEvaluation,
    CandidateRecord,
    FidelityLevel,
    MetricValue,
    ModelGenome,
    ObjectiveDirection,
    ObjectiveSpec,
    ResearchTaskContract,
)
from app.harness.discovery.novelty import DuplicateKind, NoveltyDecision
from app.harness.discovery.preflight import run_preflight
from app.harness.discovery.promotion import decide_promotion
from app.harness.discovery.stopping import (
    BudgetUsage,
    PatienceState,
    StopPolicy,
    StopReason,
    evaluate_stop,
)


def _candidate() -> tuple[ResearchTaskContract, CandidateRecord]:
    contract = ResearchTaskContract(
        run_id="run-1",
        project="example_project",
        objective="Improve a bounded model",
        allowed_paths=("src/**",),
        forbidden_paths=("src/protected/**",),
        evolution_zones=("hyperparameters.allowed",),
    )
    candidate = build_candidate_record(
        run_id="run-1",
        creator="generator",
        operator="mutate",
        genome=ModelGenome(
            family="example_family",
            hyperparameters={"allowed": 1, "private": 2},
            mutable_zones=("hyperparameters.allowed", "hyperparameters.private"),
        ),
    )
    return contract, candidate


def test_preflight_accepts_authorized_delta_and_safe_path() -> None:
    contract, candidate = _candidate()
    report = run_preflight(
        candidate=candidate,
        contract=contract,
        delta=ConfigDelta(
            (DeltaOperation("set", ("hyperparameters", "allowed"), 3),)
        ),
        touched_paths=("src/new_module.py",),
        novelty=NoveltyDecision(True),
    )

    assert report.passed
    assert not report.blockers


def test_preflight_blocks_unauthorized_delta_paths_and_duplicates() -> None:
    contract, candidate = _candidate()
    report = run_preflight(
        candidate=candidate,
        contract=contract,
        delta=ConfigDelta(
            (DeltaOperation("set", ("hyperparameters", "private"), 3),)
        ),
        touched_paths=("src/protected/base.py", "../escape.py"),
        novelty=NoveltyDecision(
            False,
            duplicate_kind=DuplicateKind.EXACT,
            matching_candidate_id="candidate-existing",
        ),
    )

    assert not report.passed
    blocker_ids = {check.check_id for check in report.blockers}
    assert "config_delta" in blocker_ids
    assert "path:src/protected/base.py" in blocker_ids
    assert "path:../escape.py" in blocker_ids
    assert "novelty" in blocker_ids


def test_preflight_rejects_tampered_implementation_fingerprint() -> None:
    contract, base_candidate = _candidate()
    implementation = "sha256:" + "1" * 64
    candidate = build_candidate_record(
        run_id=base_candidate.run_id,
        creator=base_candidate.creator,
        operator=base_candidate.operator,
        genome=base_candidate.genome,
        implementation_fingerprint=implementation,
    )
    tampered = candidate.model_copy(
        update={
            "fingerprints": {
                **candidate.fingerprints,
                "implementation": "sha256:" + "2" * 64,
            }
        }
    )

    report = run_preflight(candidate=tampered, contract=contract)

    assert not report.passed
    assert {check.check_id for check in report.blockers} == {"stable_identity"}


def test_preflight_rejects_malformed_implementation_fingerprint() -> None:
    contract, candidate = _candidate()
    malformed = candidate.model_copy(
        update={
            "fingerprints": {
                **candidate.fingerprints,
                "implementation": "sha256:not-a-digest",
            }
        }
    )

    report = run_preflight(candidate=malformed, contract=contract)

    assert not report.passed
    blocker = report.blockers[0]
    assert blocker.check_id == "stable_identity"
    assert "lowercase sha256" in blocker.reason


def test_promotion_is_direction_aware_and_stops_at_highest_fidelity() -> None:
    objectives = (
        ObjectiveSpec(
            name="error",
            direction=ObjectiveDirection.MINIMIZE,
            hard_constraint=0.2,
        ),
        ObjectiveSpec(
            name="quality",
            direction=ObjectiveDirection.MAXIMIZE,
            hard_constraint=0.8,
        ),
    )
    evaluation = CandidateEvaluation(
        evaluation_id="eval-1",
        candidate_id="candidate-1",
        run_id="run-1",
        fidelity=FidelityLevel.F1,
        seed=1,
        evaluator_hash="sha256:evaluator",
        canonical_metrics={
            "error": MetricValue(value=0.1, direction=ObjectiveDirection.MINIMIZE),
            "quality": MetricValue(value=0.9, direction=ObjectiveDirection.MAXIMIZE),
        },
        hard_constraints_passed=True,
    )

    decision = decide_promotion(evaluation, objectives)
    assert decision.promote
    assert decision.next_fidelity == FidelityLevel.F2

    blocked = decide_promotion(
        evaluation.model_copy(
            update={
                "canonical_metrics": {
                    **evaluation.canonical_metrics,
                    "quality": MetricValue(
                        value=0.7,
                        direction=ObjectiveDirection.MAXIMIZE,
                    ),
                }
            }
        ),
        objectives,
    )
    assert not blocked.promote
    assert "missed promotion threshold" in blocked.reasons[0]


def test_budget_patience_and_safety_stop_reasons_are_deterministic() -> None:
    limits = BudgetLimits(proposals=3, llm_tokens=100, wall_seconds=10.0)
    budget = evaluate_stop(
        limits=limits,
        usage=BudgetUsage(proposals=3, llm_tokens=10),
        patience=PatienceState(),
    )
    assert budget.reason == StopReason.BUDGET_EXHAUSTED
    assert budget.details == ("proposals",)

    patience = evaluate_stop(
        limits=limits,
        usage=BudgetUsage(proposals=1),
        patience=PatienceState(valid_candidates=4, since_last_improvement=3),
        policy=StopPolicy(max_without_improvement=3),
    )
    assert patience.reason == StopReason.PATIENCE_EXHAUSTED

    safety = evaluate_stop(
        limits=limits,
        usage=BudgetUsage(),
        patience=PatienceState(),
        safety_violations=("network_access", "baseline_change"),
    )
    assert safety.reason == StopReason.SAFETY_VIOLATION
    assert safety.details == ("baseline_change", "network_access")
