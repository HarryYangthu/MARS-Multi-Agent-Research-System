from __future__ import annotations

from synthetic_regression_adapter import candidate_configs, evaluate_candidate
from synthetic_regression_adapter.adapter import batch_payload, handle_request
from synthetic_regression_adapter.adapter import ContractError, fit_ridge
import pytest


def test_exactly_twenty_stable_candidates() -> None:
    first = candidate_configs()
    second = candidate_configs()

    assert first == second
    assert len(first) == 20
    assert len({candidate.candidate_id for candidate in first}) == 20
    assert len({candidate.fingerprint for candidate in first}) == 20


def test_metric_envelope_is_complete_and_repeatable() -> None:
    candidate = candidate_configs()[7]
    first = evaluate_candidate(candidate, seed=31)
    second = evaluate_candidate(candidate, seed=31)

    assert first == second
    assert first["schema_id"] == "metric_envelope.v1"
    assert set(first["raw_metrics"]) == set(first["canonical_metrics"])
    for metric in first["raw_metrics"].values():
        assert metric["unit"]
        assert metric["direction"] in {"minimize", "maximize"}
        assert metric["seed"] == 31
        assert metric["dataset_hash"].startswith("sha256:")
        assert metric["evaluator_hash"].startswith("sha256:")
        assert metric["candidate_hash"] == candidate.fingerprint


def test_adapter_protocol_and_batch() -> None:
    candidate = candidate_configs()[0]
    response = handle_request(
        {
            "protocol": "adapter.v1",
            "action": "evaluate",
            "request_id": "request-1",
            "project": "synthetic_regression",
            "candidate_id": candidate.candidate_id,
            "seed": 4,
            "config": {"candidate_index": 0},
        }
    )

    assert response["status"] == "ok"
    assert response["raw_metrics"]["schema_id"] == "metric_envelope.v1"
    assert batch_payload(count=20, seed=4)["candidate_count"] == 20


def test_fit_uses_observed_targets_and_satisfies_ridge_stationarity() -> None:
    x = [[1.0, value] for value in (-2.0, -1.0, 0.0, 1.0, 2.0)]
    y = [-4.0, -1.0, 2.0, 5.0, 8.0]
    assert fit_ridge(x, y, 0.0) == pytest.approx((2.0, 3.0))
    changed = list(y)
    changed[2] += 5
    assert fit_ridge(x, changed, 0.0) != pytest.approx((2.0, 3.0))
    regularization = 0.2
    coefficients = fit_ridge(x, changed, regularization)
    residual = [sum(a*b for a, b in zip(row, coefficients, strict=True))-target
                for row, target in zip(x, changed, strict=True)]
    gradient = [sum(row[j]*error for row, error in zip(x, residual, strict=True)) + regularization*coefficients[j]
                for j in range(2)]
    assert gradient == pytest.approx([0.0, 0.0], abs=1e-10)
    with pytest.raises(ContractError, match="rank-deficient"):
        fit_ridge([[1.0, 1.0], [1.0, 1.0]], [2.0, 3.0], 0.0)


def test_fidelity_uses_more_training_samples_and_the_same_disjoint_holdout() -> None:
    candidate = candidate_configs()[4]
    low = evaluate_candidate(candidate, seed=31, fidelity="F0")
    high = evaluate_candidate(candidate, seed=31, fidelity="F1")
    first, second = low["provenance"], high["provenance"]
    assert len(first["training_indices"]) == 6 and len(second["training_indices"]) == 8
    assert first["validation_indices"] == second["validation_indices"]
    assert set(second["validation_indices"]).isdisjoint(second["training_indices"])
    assert low["raw_metrics"]["validation_mse"]["value"] < 1e-18


@pytest.mark.parametrize("mode", ["mock", "config-only"])
def test_removed_modes_fail_without_returning_metrics(mode: str) -> None:
    response = handle_request({"protocol": "adapter.v1", "request_id": "removed-mode", "project": "synthetic_regression",
                               "action": "evaluate", "config": {"candidate_index": 0, "mode": mode}})
    assert response["status"] == "blocked" and response["raw_metrics"] == {}
