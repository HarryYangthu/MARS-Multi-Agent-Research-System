from __future__ import annotations

from synthetic_regression_adapter import candidate_configs, evaluate_candidate
from synthetic_regression_adapter.adapter import batch_payload, handle_request


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
