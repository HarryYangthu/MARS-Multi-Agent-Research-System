"""Hash-chained discovery checkpoints with pause, resume, and crash recovery."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.storage.discovery_common import (
    DiscoveryConflictError,
    DiscoveryCorruptionError,
    DiscoveryPaths,
    InvalidDiscoveryTransition,
    atomic_write_json,
    discovery_lock,
    iter_json_files,
    model_payload,
    payload_hash,
    read_json,
)


class CheckpointStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class DiscoveryCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["discovery_checkpoint.v1"] = "discovery_checkpoint.v1"
    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    phase: str = Field(min_length=1)
    iteration: int = Field(ge=0)
    status: CheckpointStatus
    state: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    idempotency_key: str = Field(min_length=1)
    previous_hash: str = ""
    checkpoint_hash: str = Field(min_length=1)
    created_at: datetime


class DiscoveryCheckpointStore:
    """Append complete checkpoints and treat ``latest.json`` as a repairable cache."""

    def __init__(self, run_root: Path, *, run_id: str) -> None:
        self.paths = DiscoveryPaths(run_root=run_root, run_id=run_id)
        self.checkpoints_dir = self.paths.root / "checkpoints" / "records"
        self.latest_path = self.paths.root / "checkpoints" / "latest.json"

    def checkpoint(
        self,
        *,
        phase: str,
        iteration: int,
        state: dict[str, Any],
        idempotency_key: str,
        expected_sequence: int | None = None,
    ) -> DiscoveryCheckpoint:
        return self.save(
            phase=phase,
            iteration=iteration,
            status=CheckpointStatus.RUNNING,
            state=state,
            idempotency_key=idempotency_key,
            expected_sequence=expected_sequence,
        )

    def save(
        self,
        *,
        phase: str,
        iteration: int,
        status: CheckpointStatus,
        state: dict[str, Any],
        idempotency_key: str,
        reason: str = "",
        expected_sequence: int | None = None,
    ) -> DiscoveryCheckpoint:
        if not phase.strip():
            raise ValueError("checkpoint phase must not be empty")
        if iteration < 0:
            raise ValueError("checkpoint iteration must be non-negative")
        if not idempotency_key.strip():
            raise ValueError("checkpoint idempotency_key must not be empty")
        with discovery_lock(self.paths):
            history = self._replay_unlocked()
            for existing in history:
                if existing.idempotency_key != idempotency_key:
                    continue
                if _same_operation(
                    existing,
                    phase=phase,
                    iteration=iteration,
                    status=status,
                    state=state,
                    reason=reason,
                ):
                    return existing
                raise DiscoveryConflictError(
                    f"checkpoint idempotency key already used: {idempotency_key}"
                )
            latest = history[-1] if history else None
            self._check_expected_sequence(latest, expected_sequence)
            if latest is not None and latest.status in {
                CheckpointStatus.COMPLETED,
                CheckpointStatus.FAILED,
            }:
                raise InvalidDiscoveryTransition(
                    f"cannot append after terminal checkpoint {latest.status.value}"
                )
            return self._append_unlocked(
                latest=latest,
                phase=phase,
                iteration=iteration,
                status=status,
                state=state,
                reason=reason,
                idempotency_key=idempotency_key,
            )

    def latest(self) -> DiscoveryCheckpoint | None:
        if not self.paths.root.exists():
            return None
        if self.latest_path.exists():
            try:
                latest = DiscoveryCheckpoint.model_validate(read_json(self.latest_path))
                self._verify_hash(latest)
                return latest
            except (DiscoveryCorruptionError, ValueError):
                pass
        history = self.replay()
        return history[-1] if history else None

    def replay(self) -> list[DiscoveryCheckpoint]:
        if not self.paths.root.exists():
            return []
        return self._replay_unlocked()

    def replay_state(self, *, sequence: int | None = None) -> dict[str, Any] | None:
        history = self.replay()
        if not history:
            return None
        if sequence is None:
            return dict(history[-1].state)
        match = next((item for item in history if item.sequence == sequence), None)
        if match is None:
            raise KeyError(sequence)
        return dict(match.state)

    def pause(
        self,
        *,
        reason: str,
        expected_sequence: int | None = None,
    ) -> DiscoveryCheckpoint:
        with discovery_lock(self.paths):
            latest = self._require_latest_unlocked()
            self._check_expected_sequence(latest, expected_sequence)
            if latest.status == CheckpointStatus.PAUSED:
                return latest
            if latest.status in {CheckpointStatus.COMPLETED, CheckpointStatus.FAILED}:
                raise InvalidDiscoveryTransition(
                    f"cannot pause a {latest.status.value} discovery run"
                )
            return self._append_unlocked(
                latest=latest,
                phase=latest.phase,
                iteration=latest.iteration,
                status=CheckpointStatus.PAUSED,
                state=latest.state,
                reason=reason,
                idempotency_key=f"pause:{latest.checkpoint_hash}",
            )

    def resume(self, *, expected_sequence: int | None = None) -> DiscoveryCheckpoint:
        with discovery_lock(self.paths):
            latest = self._require_latest_unlocked()
            self._check_expected_sequence(latest, expected_sequence)
            if latest.status == CheckpointStatus.RUNNING:
                return latest
            if latest.status != CheckpointStatus.PAUSED:
                raise InvalidDiscoveryTransition(
                    f"cannot resume a {latest.status.value} discovery run"
                )
            return self._append_unlocked(
                latest=latest,
                phase=latest.phase,
                iteration=latest.iteration,
                status=CheckpointStatus.RUNNING,
                state=latest.state,
                reason="",
                idempotency_key=f"resume:{latest.checkpoint_hash}",
            )

    def complete(
        self,
        *,
        state: dict[str, Any] | None = None,
        expected_sequence: int | None = None,
    ) -> DiscoveryCheckpoint:
        return self._finish(
            status=CheckpointStatus.COMPLETED,
            state=state,
            reason="",
            expected_sequence=expected_sequence,
        )

    def fail(
        self,
        *,
        reason: str,
        state: dict[str, Any] | None = None,
        expected_sequence: int | None = None,
    ) -> DiscoveryCheckpoint:
        return self._finish(
            status=CheckpointStatus.FAILED,
            state=state,
            reason=reason,
            expected_sequence=expected_sequence,
        )

    def recover(self) -> DiscoveryCheckpoint | None:
        """Repair ``latest`` and pause work that was running when the process died."""

        if not self.paths.root.exists():
            return None
        with discovery_lock(self.paths):
            history = self._replay_unlocked()
            if not history:
                return None
            latest = history[-1]
            atomic_write_json(self.latest_path, model_payload(latest))
            if latest.status != CheckpointStatus.RUNNING:
                return latest
            return self._append_unlocked(
                latest=latest,
                phase=latest.phase,
                iteration=latest.iteration,
                status=CheckpointStatus.PAUSED,
                state=latest.state,
                reason="crash_recovery",
                idempotency_key=f"crash-recovery:{latest.checkpoint_hash}",
            )

    def _finish(
        self,
        *,
        status: CheckpointStatus,
        state: dict[str, Any] | None,
        reason: str,
        expected_sequence: int | None,
    ) -> DiscoveryCheckpoint:
        with discovery_lock(self.paths):
            latest = self._require_latest_unlocked()
            self._check_expected_sequence(latest, expected_sequence)
            if latest.status == status:
                return latest
            if latest.status in {CheckpointStatus.COMPLETED, CheckpointStatus.FAILED}:
                raise InvalidDiscoveryTransition(
                    f"cannot change terminal checkpoint {latest.status.value} to {status.value}"
                )
            return self._append_unlocked(
                latest=latest,
                phase=latest.phase,
                iteration=latest.iteration,
                status=status,
                state=latest.state if state is None else state,
                reason=reason,
                idempotency_key=f"{status.value}:{latest.checkpoint_hash}",
            )

    def _append_unlocked(
        self,
        *,
        latest: DiscoveryCheckpoint | None,
        phase: str,
        iteration: int,
        status: CheckpointStatus,
        state: dict[str, Any],
        reason: str,
        idempotency_key: str,
    ) -> DiscoveryCheckpoint:
        sequence = 1 if latest is None else latest.sequence + 1
        previous_hash = "" if latest is None else latest.checkpoint_hash
        created_at = datetime.now(tz=timezone.utc)
        checkpoint = DiscoveryCheckpoint(
            run_id=self.paths.run_id,
            sequence=sequence,
            phase=phase,
            iteration=iteration,
            status=status,
            state=state,
            reason=reason,
            idempotency_key=idempotency_key,
            previous_hash=previous_hash,
            checkpoint_hash="pending",
            created_at=created_at,
        )
        unhashed = model_payload(checkpoint)
        unhashed.pop("checkpoint_hash")
        checkpoint = checkpoint.model_copy(
            update={"checkpoint_hash": payload_hash(unhashed)}
        )
        atomic_write_json(self._record_path(sequence), model_payload(checkpoint))
        atomic_write_json(self.latest_path, model_payload(checkpoint))
        return checkpoint

    def _replay_unlocked(self) -> list[DiscoveryCheckpoint]:
        history: list[DiscoveryCheckpoint] = []
        expected_sequence = 1
        previous_hash = ""
        for path in iter_json_files(self.checkpoints_dir):
            checkpoint = DiscoveryCheckpoint.model_validate(read_json(path))
            if checkpoint.run_id != self.paths.run_id:
                raise DiscoveryCorruptionError(f"checkpoint run_id mismatch: {path}")
            if checkpoint.sequence != expected_sequence:
                raise DiscoveryCorruptionError(
                    f"checkpoint sequence gap: expected {expected_sequence}, got {checkpoint.sequence}"
                )
            if checkpoint.previous_hash != previous_hash:
                raise DiscoveryCorruptionError(
                    f"checkpoint previous_hash mismatch at sequence {checkpoint.sequence}"
                )
            self._verify_hash(checkpoint)
            history.append(checkpoint)
            expected_sequence += 1
            previous_hash = checkpoint.checkpoint_hash
        return history

    def _verify_hash(self, checkpoint: DiscoveryCheckpoint) -> None:
        payload = model_payload(checkpoint)
        claimed = str(payload.pop("checkpoint_hash"))
        if payload_hash(payload) != claimed:
            raise DiscoveryCorruptionError(
                f"checkpoint hash mismatch at sequence {checkpoint.sequence}"
            )

    def _require_latest_unlocked(self) -> DiscoveryCheckpoint:
        history = self._replay_unlocked()
        if not history:
            raise FileNotFoundError("discovery checkpoint")
        return history[-1]

    @staticmethod
    def _check_expected_sequence(
        latest: DiscoveryCheckpoint | None,
        expected_sequence: int | None,
    ) -> None:
        if expected_sequence is None:
            return
        actual = 0 if latest is None else latest.sequence
        if actual != expected_sequence:
            raise DiscoveryConflictError(
                f"checkpoint sequence is {actual}, expected {expected_sequence}"
            )

    def _record_path(self, sequence: int) -> Path:
        return self.checkpoints_dir / f"{sequence:020d}.json"


def _same_operation(
    checkpoint: DiscoveryCheckpoint,
    *,
    phase: str,
    iteration: int,
    status: CheckpointStatus,
    state: dict[str, Any],
    reason: str,
) -> bool:
    return (
        checkpoint.phase == phase
        and checkpoint.iteration == iteration
        and checkpoint.status == status
        and checkpoint.state == state
        and checkpoint.reason == reason
    )
