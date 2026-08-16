"""Durable candidate and evaluation records for model discovery."""
from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.harness.discovery.models import (
    CandidateEvaluation,
    CandidateRecord,
    CandidateStatus,
)
from app.storage.discovery_common import (
    DiscoveryConflictError,
    DiscoveryPaths,
    InvalidDiscoveryTransition,
    atomic_write_json,
    canonical_json,
    discovery_lock,
    equivalent_model_payload,
    iter_json_files,
    model_payload,
    read_json,
    stable_key,
)


_ALLOWED_TRANSITIONS: dict[CandidateStatus, frozenset[CandidateStatus]] = {
    CandidateStatus.DRAFT: frozenset(
        {
            CandidateStatus.VALIDATED,
            CandidateStatus.REJECTED,
            CandidateStatus.QUARANTINED,
            CandidateStatus.FAILED,
        }
    ),
    CandidateStatus.VALIDATED: frozenset(
        {
            CandidateStatus.QUEUED,
            CandidateStatus.REJECTED,
            CandidateStatus.QUARANTINED,
            CandidateStatus.FAILED,
        }
    ),
    CandidateStatus.QUEUED: frozenset(
        {
            CandidateStatus.RUNNING,
            CandidateStatus.REJECTED,
            CandidateStatus.QUARANTINED,
            CandidateStatus.FAILED,
        }
    ),
    CandidateStatus.RUNNING: frozenset(
        {
            CandidateStatus.QUEUED,
            CandidateStatus.EVALUATED,
            CandidateStatus.QUARANTINED,
            CandidateStatus.FAILED,
        }
    ),
    CandidateStatus.EVALUATED: frozenset(
        {
            CandidateStatus.DOMINATED,
            CandidateStatus.ELITE,
            CandidateStatus.PROMOTED,
            CandidateStatus.QUARANTINED,
            CandidateStatus.REJECTED,
            CandidateStatus.FAILED,
        }
    ),
    CandidateStatus.DOMINATED: frozenset(
        {
            CandidateStatus.EVALUATED,
            CandidateStatus.ELITE,
            CandidateStatus.PROMOTED,
            CandidateStatus.QUARANTINED,
            CandidateStatus.REJECTED,
        }
    ),
    CandidateStatus.ELITE: frozenset(
        {
            CandidateStatus.EVALUATED,
            CandidateStatus.DOMINATED,
            CandidateStatus.PROMOTED,
            CandidateStatus.QUARANTINED,
            CandidateStatus.REJECTED,
        }
    ),
    CandidateStatus.QUARANTINED: frozenset({CandidateStatus.REJECTED}),
    CandidateStatus.REJECTED: frozenset(),
    CandidateStatus.FAILED: frozenset(
        {
            CandidateStatus.QUEUED,
            CandidateStatus.QUARANTINED,
            CandidateStatus.REJECTED,
        }
    ),
    CandidateStatus.PROMOTED: frozenset(),
}


@dataclass(frozen=True)
class CandidateRecoveryReport:
    repaired_pointers: int
    restored_evaluated: tuple[str, ...]
    requeued_interrupted: tuple[str, ...]


class CandidateStore:
    """Store candidate state with immutable history and evaluation deduplication.

    A completed evaluation is keyed by the complete deterministic evaluation
    signature.  Re-submitting that signature returns the persisted result by
    default, preventing resume/replay from spending the evaluation budget
    twice.  Callers must opt in explicitly with ``allow_rerun=True``.
    """

    def __init__(self, run_root: Path, *, run_id: str) -> None:
        self.paths = DiscoveryPaths(run_root=run_root, run_id=run_id)
        self.current_dir = self.paths.root / "candidates" / "current"
        self.history_root = self.paths.root / "candidates" / "history"
        self.evaluations_dir = self.paths.root / "evaluations"

    def get(self, candidate_id: str) -> CandidateRecord | None:
        path = self._candidate_path(candidate_id)
        if not path.exists():
            return None
        return CandidateRecord.model_validate(read_json(path))

    def list(self) -> builtins.list[CandidateRecord]:
        records = [CandidateRecord.model_validate(read_json(path)) for path in iter_json_files(self.current_dir)]
        return sorted(records, key=lambda record: (record.generation, record.iteration, record.candidate_id))

    def put(self, record: CandidateRecord) -> CandidateRecord:
        self._validate_run_id(record.run_id)
        with discovery_lock(self.paths):
            existing = self._get_unlocked(record.candidate_id)
            if existing is not None:
                if existing == record or equivalent_model_payload(
                    existing,
                    record,
                    ignored_fields=frozenset({"status", "failure_reason", "updated_at"}),
                ):
                    return existing
                raise DiscoveryConflictError(
                    f"candidate_id already contains different data: {record.candidate_id}"
                )

            for other in self._list_unlocked():
                if other.idempotency_key != record.idempotency_key:
                    continue
                if other == record:
                    return other
                raise DiscoveryConflictError(
                    f"candidate idempotency key already used: {record.idempotency_key}"
                )
            self._write_candidate_unlocked(record)
            return record

    def transition(
        self,
        candidate_id: str,
        status: CandidateStatus,
        *,
        expected_status: CandidateStatus | None = None,
        failure_reason: str | None = None,
    ) -> CandidateRecord:
        with discovery_lock(self.paths):
            current = self._require_unlocked(candidate_id)
            if expected_status is not None and current.status != expected_status:
                raise DiscoveryConflictError(
                    f"candidate {candidate_id} is {current.status.value}, expected {expected_status.value}"
                )
            if current.status == status:
                return current
            if status not in _ALLOWED_TRANSITIONS[current.status]:
                raise InvalidDiscoveryTransition(
                    f"candidate transition {current.status.value} -> {status.value} is not allowed"
                )
            return self._replace_status_unlocked(
                current,
                status,
                failure_reason=failure_reason,
            )

    def history(self, candidate_id: str) -> builtins.list[CandidateRecord]:
        directory = self._history_dir(candidate_id)
        if not directory.exists():
            return []
        return [CandidateRecord.model_validate(read_json(path)) for path in iter_json_files(directory)]

    def get_evaluation(self, evaluation_id: str) -> CandidateEvaluation | None:
        path = self._evaluation_path(evaluation_id)
        if not path.exists():
            return None
        return CandidateEvaluation.model_validate(read_json(path))

    def list_evaluations(
        self,
        *,
        candidate_id: str | None = None,
    ) -> builtins.list[CandidateEvaluation]:
        evaluations = [
            CandidateEvaluation.model_validate(read_json(path))
            for path in iter_json_files(self.evaluations_dir)
        ]
        if candidate_id is not None:
            evaluations = [item for item in evaluations if item.candidate_id == candidate_id]
        return sorted(evaluations, key=lambda item: (item.created_at, item.evaluation_id))

    def completed_evaluation(
        self,
        evaluation: CandidateEvaluation,
    ) -> CandidateEvaluation | None:
        self._validate_run_id(evaluation.run_id)
        signature = _evaluation_signature(evaluation)
        for existing in self.list_evaluations(candidate_id=evaluation.candidate_id):
            if _evaluation_signature(existing) == signature:
                return existing
        return None

    def needs_evaluation(self, evaluation: CandidateEvaluation) -> bool:
        return self.completed_evaluation(evaluation) is None

    def record_evaluation(
        self,
        evaluation: CandidateEvaluation,
        *,
        allow_rerun: bool = False,
    ) -> CandidateEvaluation:
        """Persist an evaluation and atomically make it visible to recovery."""

        self._validate_run_id(evaluation.run_id)
        with discovery_lock(self.paths):
            candidate = self._require_unlocked(evaluation.candidate_id)
            by_id = self._get_evaluation_unlocked(evaluation.evaluation_id)
            if by_id is not None:
                if not equivalent_model_payload(
                    by_id,
                    evaluation,
                    ignored_fields=frozenset({"created_at"}),
                ):
                    raise DiscoveryConflictError(
                        f"evaluation_id already contains different data: {evaluation.evaluation_id}"
                    )
                self._ensure_evaluated_unlocked(candidate)
                return by_id

            signature = _evaluation_signature(evaluation)
            if not allow_rerun:
                for existing in self._list_evaluations_unlocked(candidate_id=evaluation.candidate_id):
                    if _evaluation_signature(existing) == signature:
                        self._ensure_evaluated_unlocked(candidate)
                        return existing

            # The immutable evaluation is the commit record.  If the process
            # stops before the mutable candidate pointer is updated, recover()
            # observes this file and finishes the transition without rerunning.
            atomic_write_json(self._evaluation_path(evaluation.evaluation_id), model_payload(evaluation))
            self._ensure_evaluated_unlocked(candidate)
            return evaluation

    def recover(self) -> CandidateRecoveryReport:
        """Repair mutable pointers and safely requeue interrupted executions."""

        if not self.paths.root.exists():
            return CandidateRecoveryReport(0, (), ())
        repaired = 0
        restored: builtins.list[str] = []
        requeued: builtins.list[str] = []
        with discovery_lock(self.paths):
            if self.history_root.exists():
                for directory in sorted(path for path in self.history_root.iterdir() if path.is_dir()):
                    history_files = iter_json_files(directory)
                    if not history_files:
                        continue
                    latest = CandidateRecord.model_validate(read_json(history_files[-1]))
                    current = self._get_unlocked(latest.candidate_id)
                    if current != latest:
                        atomic_write_json(self._candidate_path(latest.candidate_id), model_payload(latest))
                        repaired += 1

            evaluations_by_candidate = {
                candidate_id: self._list_evaluations_unlocked(candidate_id=candidate_id)
                for candidate_id in {item.candidate_id for item in self._list_evaluations_unlocked()}
            }
            for candidate in self._list_unlocked():
                evaluations = evaluations_by_candidate.get(candidate.candidate_id, [])
                if evaluations and candidate.status in {
                    CandidateStatus.DRAFT,
                    CandidateStatus.VALIDATED,
                    CandidateStatus.QUEUED,
                    CandidateStatus.RUNNING,
                }:
                    self._replace_status_unlocked(candidate, CandidateStatus.EVALUATED)
                    restored.append(candidate.candidate_id)
                elif candidate.status == CandidateStatus.RUNNING:
                    self._replace_status_unlocked(candidate, CandidateStatus.QUEUED)
                    requeued.append(candidate.candidate_id)
        return CandidateRecoveryReport(
            repaired_pointers=repaired,
            restored_evaluated=tuple(sorted(restored)),
            requeued_interrupted=tuple(sorted(requeued)),
        )

    def _validate_run_id(self, run_id: str) -> None:
        if run_id != self.paths.run_id:
            raise ValueError(f"record run_id {run_id!r} does not match {self.paths.run_id!r}")

    def _candidate_path(self, candidate_id: str) -> Path:
        return self.current_dir / f"{stable_key(candidate_id)}.json"

    def _history_dir(self, candidate_id: str) -> Path:
        return self.history_root / stable_key(candidate_id)

    def _evaluation_path(self, evaluation_id: str) -> Path:
        return self.evaluations_dir / f"{stable_key(evaluation_id)}.json"

    def _get_unlocked(self, candidate_id: str) -> CandidateRecord | None:
        path = self._candidate_path(candidate_id)
        if not path.exists():
            return None
        return CandidateRecord.model_validate(read_json(path))

    def _require_unlocked(self, candidate_id: str) -> CandidateRecord:
        record = self._get_unlocked(candidate_id)
        if record is None:
            raise FileNotFoundError(candidate_id)
        return record

    def _list_unlocked(self) -> builtins.list[CandidateRecord]:
        return [CandidateRecord.model_validate(read_json(path)) for path in iter_json_files(self.current_dir)]

    def _get_evaluation_unlocked(self, evaluation_id: str) -> CandidateEvaluation | None:
        path = self._evaluation_path(evaluation_id)
        if not path.exists():
            return None
        return CandidateEvaluation.model_validate(read_json(path))

    def _list_evaluations_unlocked(
        self,
        *,
        candidate_id: str | None = None,
    ) -> builtins.list[CandidateEvaluation]:
        evaluations = [
            CandidateEvaluation.model_validate(read_json(path))
            for path in iter_json_files(self.evaluations_dir)
        ]
        if candidate_id is not None:
            return [item for item in evaluations if item.candidate_id == candidate_id]
        return evaluations

    def _write_candidate_unlocked(self, record: CandidateRecord) -> None:
        history_dir = self._history_dir(record.candidate_id)
        sequence = len(iter_json_files(history_dir)) + 1
        history_path = history_dir / f"{sequence:020d}.json"
        payload = model_payload(record)
        atomic_write_json(history_path, payload)
        atomic_write_json(self._candidate_path(record.candidate_id), payload)

    def _replace_status_unlocked(
        self,
        current: CandidateRecord,
        status: CandidateStatus,
        *,
        failure_reason: str | None = None,
    ) -> CandidateRecord:
        updates: dict[str, object] = {
            "status": status,
            "updated_at": datetime.now(tz=timezone.utc),
        }
        if failure_reason is not None:
            updates["failure_reason"] = failure_reason
        elif status != CandidateStatus.FAILED:
            updates["failure_reason"] = ""
        updated = current.model_copy(update=updates)
        self._write_candidate_unlocked(updated)
        return updated

    def _ensure_evaluated_unlocked(self, candidate: CandidateRecord) -> CandidateRecord:
        if candidate.status in {
            CandidateStatus.EVALUATED,
            CandidateStatus.DOMINATED,
            CandidateStatus.ELITE,
            CandidateStatus.PROMOTED,
            CandidateStatus.QUARANTINED,
            CandidateStatus.REJECTED,
        }:
            return candidate
        return self._replace_status_unlocked(candidate, CandidateStatus.EVALUATED)


def _evaluation_signature(evaluation: CandidateEvaluation) -> str:
    return canonical_json(
        {
            "candidate_id": evaluation.candidate_id,
            "fidelity": evaluation.fidelity.value,
            "seed": evaluation.seed,
            "evaluator_hash": evaluation.evaluator_hash,
            "dataset_hash": evaluation.dataset_hash,
            "environment_hash": evaluation.environment_hash,
            "hardware_hash": evaluation.hardware_hash,
        }
    )
