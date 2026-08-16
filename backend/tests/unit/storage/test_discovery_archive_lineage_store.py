from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.discovery.models import ArchiveSnapshot, CandidateRecord, ModelGenome
from app.storage.discovery_archive_store import ArchiveStore
from app.storage.discovery_common import DiscoveryConflictError, InvalidDiscoveryTransition
from app.storage.discovery_lineage_store import LineageStore


def _candidate(candidate_id: str, parents: tuple[str, ...] = ()) -> CandidateRecord:
    return CandidateRecord(
        candidate_id=candidate_id,
        run_id="run-1",
        parent_ids=parents,
        generation=len(parents),
        creator="unit-test",
        operator="mutation" if parents else "generate",
        genome=ModelGenome(family="tiny"),
        idempotency_key=f"candidate:{candidate_id}",
    )


def _snapshot(snapshot_id: str, iteration: int) -> ArchiveSnapshot:
    return ArchiveSnapshot(
        snapshot_id=snapshot_id,
        run_id="run-1",
        iteration=iteration,
        pareto_candidate_ids=(f"candidate-{iteration}",),
        snapshot_hash=f"sha256:{snapshot_id}",
    )


def test_lineage_queries_and_idempotent_put(tmp_path: Path) -> None:
    store = LineageStore(tmp_path / "run", run_id="run-1")
    root = store.put_candidate(_candidate("root"))
    child = store.put_candidate(_candidate("child", ("root",)))
    store.put_candidate(_candidate("grandchild", ("child",)))

    assert store.put(root) == root
    assert store.put(child) == child
    assert store.put_candidate(_candidate("root")) == root
    assert store.parents("child") == ("root",)
    assert store.children("root") == ("child",)
    assert store.ancestors("grandchild") == ("child", "root")
    assert store.descendants("root") == ("child", "grandchild")


def test_lineage_rejects_cycles_and_conflicting_rewrites(tmp_path: Path) -> None:
    store = LineageStore(tmp_path / "run", run_id="run-1")
    store.put_candidate(_candidate("a", ("b",)))

    with pytest.raises(InvalidDiscoveryTransition):
        store.put_candidate(_candidate("b", ("a",)))
    with pytest.raises(DiscoveryConflictError):
        store.put_candidate(_candidate("a", ("other",)))


def test_archive_is_append_only_sorted_and_replayable(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "run", run_id="run-1")
    second = _snapshot("snapshot-2", 2)
    first = _snapshot("snapshot-1", 1)

    assert store.list() == []
    assert store.latest() is None
    assert store.put(second) == second
    assert store.put(first) == first
    assert store.put(first) == first
    assert store.put(_snapshot("snapshot-1", 1)) == first
    assert [item.snapshot_id for item in store.list()] == ["snapshot-1", "snapshot-2"]
    assert store.latest() == second
    assert store.replay(from_iteration=2) == [second]
    assert store.find_by_hash(first.snapshot_hash) == first
    assert ArchiveStore.calculate_snapshot_hash(first).startswith("sha256:")

    with pytest.raises(DiscoveryConflictError):
        store.put(first.model_copy(update={"stop_reason": "different"}))


def test_legacy_archive_and_lineage_reads_do_not_create_directory(tmp_path: Path) -> None:
    run_root = tmp_path / "legacy"
    run_root.mkdir()

    assert ArchiveStore(run_root, run_id="run-1").list() == []
    assert LineageStore(run_root, run_id="run-1").list() == []
    assert not (run_root / "discovery").exists()
