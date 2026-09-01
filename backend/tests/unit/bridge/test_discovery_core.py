from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import pytest

from app.bridge.discovery_core import DefaultDiscoveryCore
from app.execution.adapters.base import AdapterResponse
from app.harness.discovery.candidate_builder import build_candidate_record
from app.harness.discovery.canonical import stable_hash
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
from app.storage.discovery_budget_ledger import BudgetLedger


def _contract() -> ResearchTaskContract:
    return ResearchTaskContract(
        run_id="run-envelope",
        project="synthetic_regression",
        objective="minimize score",
        evaluator_hash="sha256:evaluator",
        dataset_hash="sha256:dataset",
        objectives=(
            ObjectiveSpec(
                name="score",
                direction=ObjectiveDirection.MINIMIZE,
                unit="normalized",
            ),
        ),
        seed=17,
    )


def _candidate() -> CandidateRecord:
    return build_candidate_record(
        run_id="run-envelope",
        genome=ModelGenome(family="synthetic_regression"),
        creator="unit_test",
        operator="generate",
    )


def _envelope() -> dict[str, Any]:
    common: dict[str, Any] = {
        "seed": 17,
        "evaluator_hash": "sha256:evaluator",
        "dataset_hash": "sha256:dataset",
    }
    envelope: dict[str, Any] = {
        "schema_id": "metric_envelope.v1",
        "raw_metrics": {
            "score": {
                "raw_key": "score",
                "value": 0.25,
                "unit": "raw",
                "direction": "minimize",
                **common,
            }
        },
        "canonical_metrics": {
            "score": {
                "value": 0.25,
                "unit": "normalized",
                "direction": "minimize",
                **common,
            }
        },
        "provenance": common,
    }
    envelope["envelope_hash"] = stable_hash(envelope)
    return envelope


def _evaluate(raw_metrics: dict[str, Any]) -> CandidateEvaluation:
    return DefaultDiscoveryCore().evaluate(
        candidate=_candidate(),
        contract=_contract(),
        response=AdapterResponse(
            request_id="evaluate-envelope",
            status="ok",
            raw_metrics=raw_metrics,
        ),
        fidelity=FidelityLevel.F0,
        seed=17,
    )


def _rehash(envelope: dict[str, Any]) -> None:
    envelope.pop("envelope_hash", None)
    envelope["envelope_hash"] = stable_hash(envelope)


def test_valid_metric_envelope_is_canonicalized_and_preserved() -> None:
    envelope = _envelope()

    evaluation = _evaluate(envelope)

    assert evaluation.hard_constraints_passed is True
    assert evaluation.canonical_metrics["score"].value == pytest.approx(0.25)
    assert evaluation.canonical_metrics["score"].unit == "normalized"
    assert evaluation.canonical_metrics["score"].direction == ObjectiveDirection.MINIMIZE
    assert evaluation.raw_metrics == envelope
    assert evaluation.raw_metrics["provenance"]["seed"] == 17
    assert evaluation.raw_metrics["envelope_hash"] == envelope["envelope_hash"]


Mutation = Callable[[dict[str, Any]], None]


def _missing_metric(envelope: dict[str, Any]) -> None:
    del envelope["canonical_metrics"]["score"]


def _nan_metric(envelope: dict[str, Any]) -> None:
    envelope["canonical_metrics"]["score"]["value"] = float("nan")


def _wrong_unit(envelope: dict[str, Any]) -> None:
    envelope["canonical_metrics"]["score"]["unit"] = "percent"


def _wrong_direction(envelope: dict[str, Any]) -> None:
    envelope["canonical_metrics"]["score"]["direction"] = "maximize"


def _wrong_seed(envelope: dict[str, Any]) -> None:
    envelope["provenance"]["seed"] = 99
    envelope["canonical_metrics"]["score"]["seed"] = 99


def _wrong_evaluator(envelope: dict[str, Any]) -> None:
    envelope["provenance"]["evaluator_hash"] = "sha256:wrong"
    envelope["canonical_metrics"]["score"]["evaluator_hash"] = "sha256:wrong"


def _wrong_dataset(envelope: dict[str, Any]) -> None:
    envelope["provenance"]["dataset_hash"] = "sha256:wrong"
    envelope["canonical_metrics"]["score"]["dataset_hash"] = "sha256:wrong"


def _wrong_metric_provenance(envelope: dict[str, Any]) -> None:
    envelope["canonical_metrics"]["score"]["seed"] = 99


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(_missing_metric, id="missing-objective"),
        pytest.param(_nan_metric, id="nan"),
        pytest.param(_wrong_unit, id="unit"),
        pytest.param(_wrong_direction, id="direction"),
        pytest.param(_wrong_seed, id="provenance-seed"),
        pytest.param(_wrong_evaluator, id="provenance-evaluator"),
        pytest.param(_wrong_dataset, id="provenance-dataset"),
        pytest.param(_wrong_metric_provenance, id="metric-provenance"),
    ],
)
def test_invalid_metric_envelope_fails_closed(mutation: Mutation) -> None:
    envelope = deepcopy(_envelope())
    mutation(envelope)
    _rehash(envelope)

    evaluation = _evaluate(envelope)

    assert evaluation.hard_constraints_passed is False
    assert evaluation.canonical_metrics == {}
    assert evaluation.findings
    assert evaluation.raw_metrics == envelope


def test_metric_envelope_hash_mismatch_fails_closed() -> None:
    envelope = _envelope()
    envelope["envelope_hash"] = "sha256:tampered"

    evaluation = _evaluate(envelope)

    assert evaluation.hard_constraints_passed is False
    assert evaluation.canonical_metrics == {}
    assert "metric envelope envelope_hash mismatch" in evaluation.findings


def test_legacy_flat_metrics_remain_supported() -> None:
    evaluation = _evaluate({"score": 0.5})

    assert evaluation.hard_constraints_passed is True
    assert evaluation.canonical_metrics["score"].value == pytest.approx(0.5)
    assert evaluation.raw_metrics == {"score": 0.5}


def test_archive_uses_highest_fidelity_shared_seed_cohort_not_last_seed(
    tmp_path: Path,
) -> None:
    contract = _contract()

    def evaluation(candidate_id: str, seed: int, value: float) -> CandidateEvaluation:
        return CandidateEvaluation(
            evaluation_id=f"{candidate_id}-{seed}",
            candidate_id=candidate_id,
            run_id=contract.run_id,
            fidelity=FidelityLevel.F0,
            seed=seed,
            evaluator_hash=contract.evaluator_hash,
            dataset_hash=contract.dataset_hash,
            canonical_metrics={
                "score": MetricValue(
                    value=value,
                    unit="normalized",
                    direction=ObjectiveDirection.MINIMIZE,
                )
            },
            hard_constraints_passed=True,
        )

    candidate_a = tuple(
        evaluation("candidate-a", seed, 30.0 if seed < 30 else 40.01)
        for seed in range(31)
    )
    candidate_b = tuple(
        evaluation("candidate-b", seed, 40.0) for seed in range(31)
    )
    budget = BudgetLedger(
        tmp_path,
        run_id=contract.run_id,
        limits=contract.budget,
    ).snapshot()

    snapshot = DefaultDiscoveryCore().archive(
        contract=contract,
        evaluations=(*candidate_a, *candidate_b),
        iteration=0,
        budget=budget,
        quarantined_candidate_ids=(),
    )

    assert snapshot.pareto_candidate_ids == ("candidate-a",)
