from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.harness.discovery.models import (
    CandidateEvaluation,
    CandidateRecord,
    CandidateStatus,
    FidelityLevel,
    ModelGenome,
)
from app.storage.discovery_candidate_store import CandidateStore
from app.storage.discovery_common import (
    DiscoveryConflictError,
    InvalidDiscoveryTransition,
    stable_key,
)


def _candidate(
    candidate_id: str,
    *,
    run_id: str = "run-1",
    idempotency_key: str | None = None,
    parent_ids: tuple[str, ...] = (),
) -> CandidateRecord:
    return CandidateRecord(
        candidate_id=candidate_id,
        run_id=run_id,
        parent_ids=parent_ids,
        creator="unit-test",
        operator="generate",
        genome=ModelGenome(family="tiny", structure={"width": 4}),
        idempotency_key=idempotency_key or f"candidate:{candidate_id}",
    )


def _evaluation(
    evaluation_id: str,
    candidate_id: str,
    *,
    run_id: str = "run-1",
) -> CandidateEvaluation:
    return CandidateEvaluation(
        evaluation_id=evaluation_id,
        candidate_id=candidate_id,
        run_id=run_id,
        fidelity=FidelityLevel.F1,
        seed=7,
        evaluator_hash="sha256:evaluator",
        dataset_hash="sha256:dataset",
        environment_hash="sha256:environment",
        hardware_hash="sha256:hardware",
    )


def _running(store: CandidateStore, candidate_id: str) -> CandidateRecord:
    store.put(_candidate(candidate_id))
    store.transition(candidate_id, CandidateStatus.VALIDATED)
    store.transition(candidate_id, CandidateStatus.QUEUED)
    return store.transition(candidate_id, CandidateStatus.RUNNING)


def test_old_run_without_discovery_directory_is_readable(tmp_path: Path) -> None:
    run_root = tmp_path / "legacy-run"
    run_root.mkdir()
    store = CandidateStore(run_root, run_id="run-1")

    assert store.list() == []
    assert store.list_evaluations() == []
    assert store.get("missing") is None
    assert not (run_root / "discovery").exists()


def test_candidate_put_transition_history_and_idempotency(tmp_path: Path) -> None:
    store = CandidateStore(tmp_path / "run", run_id="run-1")
    record = _candidate("candidate-1")

    assert store.put(record) == record
    assert store.put(record) == record
    validated = store.transition(
        "candidate-1",
        CandidateStatus.VALIDATED,
        expected_status=CandidateStatus.DRAFT,
    )
    assert validated.status == CandidateStatus.VALIDATED
    assert store.transition("candidate-1", CandidateStatus.VALIDATED) == validated
    assert store.put(record) == validated
    assert [item.status for item in store.history("candidate-1")] == [
        CandidateStatus.DRAFT,
        CandidateStatus.VALIDATED,
    ]

    with pytest.raises(InvalidDiscoveryTransition):
        store.transition("candidate-1", CandidateStatus.PROMOTED)
    with pytest.raises(DiscoveryConflictError):
        store.put(_candidate("candidate-2", idempotency_key=record.idempotency_key))


def test_candidate_ids_cannot_escape_store_directory(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    store = CandidateStore(run_root, run_id="run-1")
    store.put(_candidate("../../outside"))

    assert store.get("../../outside") is not None
    assert not (tmp_path / "outside.json").exists()


def test_completed_evaluation_is_not_rerun_by_default(tmp_path: Path) -> None:
    store = CandidateStore(tmp_path / "run", run_id="run-1")
    _running(store, "candidate-1")
    first = _evaluation("evaluation-1", "candidate-1")
    same_contract_new_id = _evaluation("evaluation-2", "candidate-1")

    assert store.record_evaluation(first) == first
    assert store.needs_evaluation(same_contract_new_id) is False
    assert store.record_evaluation(same_contract_new_id) == first
    assert len(store.list_evaluations(candidate_id="candidate-1")) == 1
    persisted = store.get("candidate-1")
    assert persisted is not None
    assert persisted.status == CandidateStatus.EVALUATED

    assert store.record_evaluation(same_contract_new_id, allow_rerun=True) == same_contract_new_id
    assert len(store.list_evaluations(candidate_id="candidate-1")) == 2


def test_recovery_finishes_committed_evaluation_and_requeues_interrupted_work(
    tmp_path: Path,
) -> None:
    store = CandidateStore(tmp_path / "run", run_id="run-1")
    _running(store, "evaluated-after-crash")
    _running(store, "interrupted")
    evaluation = _evaluation("evaluation-crash", "evaluated-after-crash")

    store.evaluations_dir.mkdir(parents=True)
    path = store.evaluations_dir / f"{stable_key(evaluation.evaluation_id)}.json"
    path.write_text(json.dumps(evaluation.model_dump(mode="json")), encoding="utf-8")

    report = store.recover()

    assert report.restored_evaluated == ("evaluated-after-crash",)
    assert report.requeued_interrupted == ("interrupted",)
    evaluated = store.get("evaluated-after-crash")
    interrupted = store.get("interrupted")
    assert evaluated is not None and evaluated.status == CandidateStatus.EVALUATED
    assert interrupted is not None and interrupted.status == CandidateStatus.QUEUED


def test_recovery_rebuilds_missing_current_pointer(tmp_path: Path) -> None:
    store = CandidateStore(tmp_path / "run", run_id="run-1")
    store.put(_candidate("candidate-1"))
    next(store.current_dir.glob("*.json")).unlink()

    report = store.recover()

    assert report.repaired_pointers == 1
    assert store.get("candidate-1") is not None


def test_concurrent_candidate_writes_do_not_lose_records(tmp_path: Path) -> None:
    store = CandidateStore(tmp_path / "run", run_id="run-1")

    def write(index: int) -> str:
        return store.put(_candidate(f"candidate-{index:02d}")).candidate_id

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert len(set(executor.map(write, range(32)))) == 32

    assert len(store.list()) == 32
