from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.discovery.models import FidelityLevel
from app.storage.discovery_common import InvalidDiscoveryTransition
from app.storage.discovery_promotion_store import (
    PromotionTask,
    PromotionTaskState,
    PromotionTaskStore,
)


def _task(run_id: str, *, task_id: str = "promotion-1") -> PromotionTask:
    return PromotionTask(
        task_id=task_id,
        run_id=run_id,
        candidate_id="candidate-1",
        source_evaluation_id="evaluation-f0",
        from_fidelity=FidelityLevel.F0,
        to_fidelity=FidelityLevel.F1,
        seed=7,
        evaluator_hash="sha256:evaluator",
        dataset_hash="sha256:dataset",
        aggregate_refs=("discovery/evaluation_aggregates/evidence.json",),
        policy_hash="sha256:policy",
        idempotency_key="promotion-idempotency-1",
    )


def test_enqueue_is_idempotent_and_recovery_requeues_running_task(
    tmp_path: Path,
) -> None:
    store = PromotionTaskStore(tmp_path, run_id="run-1")

    first, created = store.enqueue(_task("run-1"))
    repeated, repeated_created = store.enqueue(_task("run-1"))
    running = store.transition(
        first.task_id,
        PromotionTaskState.RUNNING,
        expected_state=PromotionTaskState.PENDING,
    )
    recovered_ids = store.recover()
    recovered = store.get(first.task_id)

    assert created is True
    assert repeated_created is False
    assert repeated.task_id == first.task_id
    assert running.attempts == 1
    assert recovered_ids == (first.task_id,)
    assert recovered is not None
    assert recovered.state == PromotionTaskState.PENDING
    assert recovered.attempts == 1
    assert recovered.last_error == "recovered_after_interruption"

    rerunning = store.transition(
        first.task_id,
        PromotionTaskState.RUNNING,
        expected_state=PromotionTaskState.PENDING,
    )
    completed = store.transition(
        first.task_id,
        PromotionTaskState.COMPLETED,
        expected_state=PromotionTaskState.RUNNING,
        result_evaluation_id="evaluation-f1",
    )

    assert rerunning.attempts == 2
    assert completed.result_evaluation_id == "evaluation-f1"
    assert completed.state == PromotionTaskState.COMPLETED


def test_cancel_pending_is_terminal_and_audited(tmp_path: Path) -> None:
    store = PromotionTaskStore(tmp_path, run_id="run-1")
    task, _ = store.enqueue(_task("run-1"))

    cancelled_ids = store.cancel_pending(reason="manual stop")
    cancelled = store.get(task.task_id)

    assert cancelled_ids == (task.task_id,)
    assert cancelled is not None
    assert cancelled.state == PromotionTaskState.CANCELLED
    assert cancelled.last_error == "manual stop"
    with pytest.raises(InvalidDiscoveryTransition, match="not allowed"):
        store.transition(task.task_id, PromotionTaskState.RUNNING)


def test_enqueue_rejects_skipped_fidelity_and_wrong_run(tmp_path: Path) -> None:
    store = PromotionTaskStore(tmp_path, run_id="run-1")

    with pytest.raises(ValueError, match="exactly one fidelity"):
        store.enqueue(
            _task("run-1").model_copy(
                update={"to_fidelity": FidelityLevel.F2}
            )
        )
    with pytest.raises(ValueError, match="run_id"):
        store.enqueue(_task("another-run"))
