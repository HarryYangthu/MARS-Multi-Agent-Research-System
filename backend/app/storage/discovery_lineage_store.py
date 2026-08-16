"""Immutable parent/child lineage storage for discovery candidates."""
from __future__ import annotations

import builtins
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.harness.discovery.models import CandidateRecord
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


class LineageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["candidate_lineage.v1"] = "candidate_lineage.v1"
    run_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    parent_ids: tuple[str, ...] = ()
    generation: int = Field(ge=0)
    operator: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class LineageStore:
    """Record immutable lineage and answer graph queries without upper imports."""

    def __init__(self, run_root: Path, *, run_id: str) -> None:
        self.paths = DiscoveryPaths(run_root=run_root, run_id=run_id)
        self.records_dir = self.paths.root / "lineage" / "records"

    def put_candidate(self, candidate: CandidateRecord) -> LineageRecord:
        if candidate.run_id != self.paths.run_id:
            raise ValueError("candidate run_id does not match lineage run")
        record = LineageRecord(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            parent_ids=candidate.parent_ids,
            generation=candidate.generation,
            operator=candidate.operator,
            created_at=candidate.created_at,
        )
        return self.put(record)

    def put(self, record: LineageRecord) -> LineageRecord:
        self._validate(record)
        with discovery_lock(self.paths):
            existing = self._get_unlocked(record.candidate_id)
            if existing is not None:
                if existing == record or equivalent_model_payload(
                    existing,
                    record,
                    ignored_fields=frozenset({"created_at"}),
                ):
                    return existing
                raise DiscoveryConflictError(
                    f"lineage is immutable for candidate {record.candidate_id}"
                )
            graph = {item.candidate_id: item.parent_ids for item in self._list_unlocked()}
            graph[record.candidate_id] = record.parent_ids
            if _contains_cycle(graph, record.candidate_id):
                raise InvalidDiscoveryTransition(
                    f"lineage would create a cycle at candidate {record.candidate_id}"
                )
            atomic_write_json(self._path(record.candidate_id), model_payload(record))
            return record

    def get(self, candidate_id: str) -> LineageRecord | None:
        path = self._path(candidate_id)
        if not path.exists():
            return None
        return LineageRecord.model_validate(read_json(path))

    def list(self) -> builtins.list[LineageRecord]:
        records = [LineageRecord.model_validate(read_json(path)) for path in iter_json_files(self.records_dir)]
        return sorted(records, key=lambda item: (item.generation, item.candidate_id))

    def parents(self, candidate_id: str) -> tuple[str, ...]:
        record = self.get(candidate_id)
        return record.parent_ids if record is not None else ()

    def children(self, candidate_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(record.candidate_id for record in self.list() if candidate_id in record.parent_ids)
        )

    def ancestors(self, candidate_id: str) -> tuple[str, ...]:
        graph = {record.candidate_id: record.parent_ids for record in self.list()}
        return tuple(sorted(_walk_parents(graph, candidate_id)))

    def descendants(self, candidate_id: str) -> tuple[str, ...]:
        children_by_parent: dict[str, set[str]] = {}
        for record in self.list():
            for parent_id in record.parent_ids:
                children_by_parent.setdefault(parent_id, set()).add(record.candidate_id)
        seen: set[str] = set()
        pending = builtins.list(children_by_parent.get(candidate_id, set()))
        while pending:
            child_id = pending.pop()
            if child_id in seen:
                continue
            seen.add(child_id)
            pending.extend(children_by_parent.get(child_id, set()))
        return tuple(sorted(seen))

    def _path(self, candidate_id: str) -> Path:
        return self.records_dir / f"{stable_key(candidate_id)}.json"

    def _get_unlocked(self, candidate_id: str) -> LineageRecord | None:
        path = self._path(candidate_id)
        if not path.exists():
            return None
        return LineageRecord.model_validate(read_json(path))

    def _list_unlocked(self) -> builtins.list[LineageRecord]:
        return [LineageRecord.model_validate(read_json(path)) for path in iter_json_files(self.records_dir)]

    def _validate(self, record: LineageRecord) -> None:
        if record.run_id != self.paths.run_id:
            raise ValueError("lineage run_id does not match store run")
        if record.candidate_id in record.parent_ids:
            raise InvalidDiscoveryTransition("candidate cannot be its own parent")
        if len(set(record.parent_ids)) != len(record.parent_ids):
            raise ValueError("lineage parent_ids must be unique")


def _contains_cycle(graph: dict[str, tuple[str, ...]], candidate_id: str) -> bool:
    return candidate_id in _walk_parents(graph, candidate_id, include_start_edges=True)


def _walk_parents(
    graph: dict[str, tuple[str, ...]],
    candidate_id: str,
    *,
    include_start_edges: bool = False,
) -> set[str]:
    seen: set[str] = set()
    pending = builtins.list(graph.get(candidate_id, ()))
    while pending:
        parent_id = pending.pop()
        if parent_id in seen:
            continue
        seen.add(parent_id)
        pending.extend(graph.get(parent_id, ()))
    if not include_start_edges:
        seen.discard(candidate_id)
    return seen
