"""Immutable Pareto/MAP-Elites archive snapshots."""
from __future__ import annotations

import builtins
from pathlib import Path

from app.harness.discovery.models import ArchiveSnapshot
from app.storage.discovery_common import (
    DiscoveryConflictError,
    DiscoveryPaths,
    atomic_write_json,
    discovery_lock,
    equivalent_model_payload,
    iter_json_files,
    model_payload,
    payload_hash,
    read_json,
    stable_key,
)


class ArchiveStore:
    """Persist append-only snapshots and replay archive evolution by iteration."""

    def __init__(self, run_root: Path, *, run_id: str) -> None:
        self.paths = DiscoveryPaths(run_root=run_root, run_id=run_id)
        self.snapshots_dir = self.paths.root / "archive" / "snapshots"

    def put(self, snapshot: ArchiveSnapshot) -> ArchiveSnapshot:
        if snapshot.run_id != self.paths.run_id:
            raise ValueError("archive snapshot run_id does not match store run")
        with discovery_lock(self.paths):
            existing = self._get_unlocked(snapshot.snapshot_id)
            if existing is not None:
                if existing == snapshot or equivalent_model_payload(
                    existing,
                    snapshot,
                    ignored_fields=frozenset({"created_at"}),
                ):
                    return existing
                raise DiscoveryConflictError(
                    f"archive snapshot is immutable: {snapshot.snapshot_id}"
                )
            atomic_write_json(self._path(snapshot.snapshot_id), model_payload(snapshot))
            return snapshot

    def get(self, snapshot_id: str) -> ArchiveSnapshot | None:
        path = self._path(snapshot_id)
        if not path.exists():
            return None
        return ArchiveSnapshot.model_validate(read_json(path))

    def find_by_hash(self, snapshot_hash: str) -> ArchiveSnapshot | None:
        return next((item for item in self.list() if item.snapshot_hash == snapshot_hash), None)

    def list(self) -> builtins.list[ArchiveSnapshot]:
        snapshots = [
            ArchiveSnapshot.model_validate(read_json(path))
            for path in iter_json_files(self.snapshots_dir)
        ]
        return sorted(snapshots, key=lambda item: (item.iteration, item.created_at, item.snapshot_id))

    def latest(self) -> ArchiveSnapshot | None:
        snapshots = self.list()
        return snapshots[-1] if snapshots else None

    def replay(
        self,
        *,
        from_iteration: int = 0,
        through_iteration: int | None = None,
    ) -> builtins.list[ArchiveSnapshot]:
        if from_iteration < 0:
            raise ValueError("from_iteration must be non-negative")
        if through_iteration is not None and through_iteration < from_iteration:
            raise ValueError("through_iteration must not precede from_iteration")
        return [
            item
            for item in self.list()
            if item.iteration >= from_iteration
            and (through_iteration is None or item.iteration <= through_iteration)
        ]

    @staticmethod
    def calculate_snapshot_hash(snapshot: ArchiveSnapshot) -> str:
        payload = model_payload(snapshot)
        payload.pop("snapshot_hash", None)
        return payload_hash(payload)

    def _path(self, snapshot_id: str) -> Path:
        return self.snapshots_dir / f"{stable_key(snapshot_id)}.json"

    def _get_unlocked(self, snapshot_id: str) -> ArchiveSnapshot | None:
        path = self._path(snapshot_id)
        if not path.exists():
            return None
        return ArchiveSnapshot.model_validate(read_json(path))
