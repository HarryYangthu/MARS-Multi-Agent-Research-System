"""Durable, idempotent next-fidelity tasks for Discovery promotion."""
from __future__ import annotations

import builtins
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.harness.discovery.models import FidelityLevel
from app.storage.discovery_common import (
    DiscoveryConflictError,
    DiscoveryPaths,
    InvalidDiscoveryTransition,
    atomic_write_json,
    discovery_lock,
    equivalent_model_payload,
    iter_json_files,
    model_payload,
    read_json,
    stable_key,
)


class PromotionTaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PromotionTask(BaseModel):
    """One deterministic evaluation transition from fidelity N to N+1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["promotion_task.v1"] = "promotion_task.v1"
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    source_evaluation_id: str = Field(min_length=1)
    from_fidelity: FidelityLevel
    to_fidelity: FidelityLevel
    seed: int
    evaluator_hash: str = Field(min_length=1)
    dataset_hash: str = ""
    purpose: Literal["candidate_promotion", "statistical_baseline"] = (
        "candidate_promotion"
    )
    aggregate_refs: tuple[str, ...] = ()
    policy_hash: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    state: PromotionTaskState = PromotionTaskState.PENDING
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=5)
    result_evaluation_id: str = ""
    last_error: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class PromotionTaskStore:
    """Persist promotion tasks with atomic current pointers and full history."""

    def __init__(self, run_root: Path, *, run_id: str) -> None:
        self.paths = DiscoveryPaths(run_root=run_root, run_id=run_id)
        self.current_dir = self.paths.root / "promotions" / "current"
        self.history_root = self.paths.root / "promotions" / "history"

    def get(self, task_id: str) -> PromotionTask | None:
        path = self._task_path(task_id)
        if not path.exists():
            return None
        return PromotionTask.model_validate(read_json(path))

    def list(
        self,
        *,
        state: PromotionTaskState | None = None,
    ) -> builtins.list[PromotionTask]:
        tasks = [
            PromotionTask.model_validate(read_json(path))
            for path in iter_json_files(self.current_dir)
        ]
        if state is not None:
            tasks = [task for task in tasks if task.state == state]
        return sorted(tasks, key=lambda task: (task.created_at, task.task_id))

    def enqueue(self, task: PromotionTask) -> tuple[PromotionTask, bool]:
        self._validate_task(task)
        with discovery_lock(self.paths):
            existing = self._get_unlocked(task.task_id)
            if existing is not None:
                if equivalent_model_payload(
                    existing,
                    task,
                    ignored_fields=frozenset(
                        {
                            "state",
                            "attempts",
                            "result_evaluation_id",
                            "last_error",
                            "created_at",
                            "updated_at",
                        }
                    ),
                ):
                    return existing, False
                raise DiscoveryConflictError(
                    f"promotion task_id already contains different data: {task.task_id}"
                )
            for other in self._list_unlocked():
                if other.idempotency_key != task.idempotency_key:
                    continue
                if equivalent_model_payload(
                    other,
                    task,
                    ignored_fields=frozenset(
                        {
                            "task_id",
                            "state",
                            "attempts",
                            "result_evaluation_id",
                            "last_error",
                            "created_at",
                            "updated_at",
                        }
                    ),
                ):
                    return other, False
                raise DiscoveryConflictError(
                    "promotion idempotency key is already bound to another task"
                )
            self._write_unlocked(task)
            return task, True

    def transition(
        self,
        task_id: str,
        state: PromotionTaskState,
        *,
        expected_state: PromotionTaskState | None = None,
        result_evaluation_id: str = "",
        last_error: str = "",
    ) -> PromotionTask:
        with discovery_lock(self.paths):
            current = self._require_unlocked(task_id)
            if expected_state is not None and current.state != expected_state:
                raise DiscoveryConflictError(
                    f"promotion task {task_id} is {current.state.value}, "
                    f"expected {expected_state.value}"
                )
            if current.state == state:
                return current
            allowed = {
                PromotionTaskState.PENDING: frozenset(
                    {
                        PromotionTaskState.RUNNING,
                        PromotionTaskState.FAILED,
                        PromotionTaskState.CANCELLED,
                    }
                ),
                PromotionTaskState.RUNNING: frozenset(
                    {
                        PromotionTaskState.PENDING,
                        PromotionTaskState.COMPLETED,
                        PromotionTaskState.FAILED,
                        PromotionTaskState.CANCELLED,
                    }
                ),
                PromotionTaskState.COMPLETED: frozenset(),
                PromotionTaskState.FAILED: frozenset(),
                PromotionTaskState.CANCELLED: frozenset(),
            }
            if state not in allowed[current.state]:
                raise InvalidDiscoveryTransition(
                    f"promotion transition {current.state.value} -> {state.value} "
                    "is not allowed"
                )
            updates: dict[str, object] = {
                "state": state,
                "updated_at": datetime.now(tz=timezone.utc),
                "last_error": last_error,
            }
            if state == PromotionTaskState.RUNNING:
                updates["attempts"] = current.attempts + 1
            if result_evaluation_id:
                updates["result_evaluation_id"] = result_evaluation_id
            updated = current.model_copy(update=updates)
            self._write_unlocked(updated)
            return updated

    def recover(self) -> tuple[str, ...]:
        """Requeue tasks interrupted after their durable RUNNING transition."""

        if not self.paths.root.exists():
            return ()
        recovered: builtins.list[str] = []
        with discovery_lock(self.paths):
            for task in self._list_unlocked():
                if task.state != PromotionTaskState.RUNNING:
                    continue
                updated = task.model_copy(
                    update={
                        "state": PromotionTaskState.PENDING,
                        "last_error": "recovered_after_interruption",
                        "updated_at": datetime.now(tz=timezone.utc),
                    }
                )
                self._write_unlocked(updated)
                recovered.append(task.task_id)
        return tuple(sorted(recovered))

    def cancel_pending(self, *, reason: str) -> tuple[str, ...]:
        cancelled: builtins.list[str] = []
        with discovery_lock(self.paths):
            for task in self._list_unlocked():
                if task.state != PromotionTaskState.PENDING:
                    continue
                updated = task.model_copy(
                    update={
                        "state": PromotionTaskState.CANCELLED,
                        "last_error": reason,
                        "updated_at": datetime.now(tz=timezone.utc),
                    }
                )
                self._write_unlocked(updated)
                cancelled.append(task.task_id)
        return tuple(sorted(cancelled))

    def _validate_task(self, task: PromotionTask) -> None:
        if task.run_id != self.paths.run_id:
            raise ValueError("promotion task run_id does not match store run")
        if _fidelity_rank(task.to_fidelity) != _fidelity_rank(task.from_fidelity) + 1:
            raise ValueError("promotion task must advance exactly one fidelity level")
        if task.state != PromotionTaskState.PENDING or task.attempts != 0:
            raise ValueError("new promotion tasks must be pending with zero attempts")

    def _task_path(self, task_id: str) -> Path:
        return self.current_dir / f"{stable_key(task_id)}.json"

    def _history_dir(self, task_id: str) -> Path:
        return self.history_root / stable_key(task_id)

    def _get_unlocked(self, task_id: str) -> PromotionTask | None:
        path = self._task_path(task_id)
        if not path.exists():
            return None
        return PromotionTask.model_validate(read_json(path))

    def _require_unlocked(self, task_id: str) -> PromotionTask:
        task = self._get_unlocked(task_id)
        if task is None:
            raise FileNotFoundError(task_id)
        return task

    def _list_unlocked(self) -> builtins.list[PromotionTask]:
        return [
            PromotionTask.model_validate(read_json(path))
            for path in iter_json_files(self.current_dir)
        ]

    def _write_unlocked(self, task: PromotionTask) -> None:
        history_dir = self._history_dir(task.task_id)
        sequence = len(iter_json_files(history_dir)) + 1
        payload = model_payload(task)
        atomic_write_json(history_dir / f"{sequence:020d}.json", payload)
        atomic_write_json(self._task_path(task.task_id), payload)


def _fidelity_rank(value: FidelityLevel) -> int:
    return int(value.value[1:])
