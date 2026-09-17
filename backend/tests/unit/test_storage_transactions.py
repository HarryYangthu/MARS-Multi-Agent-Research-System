"""Real filesystem/process concurrency and damaged-pointer recovery contracts."""
from __future__ import annotations

import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.harness.persistence import atomic_write_json, path_lock
from app.harness.runtime.run_graph import RunGraph
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.storage.artifact_store import ArtifactConflictError, ArtifactCorruptionError, ArtifactStore
from app.storage.run_state_store import RunStateConflictError, RunStateStore
from app.storage.run_store import RunStore


def _proposal(index: int) -> str:
    return fm_dumps({"schema": "proposal.v1", "project": "pimc", "agent": "idea",
                     "research_question": "Which branch is measurable?",
                     "hypothesis": "A constrained branch may reduce parameters.",
                     "novelty": "Unvalidated hypothesis for a controlled experiment."}, f"Draft {index}\n")


def _write_artifact(args: tuple[str, str, int]) -> str:
    root, run_id, index = args
    run = RunStore(Path(root)).get(run_id)
    assert run is not None
    return ArtifactStore(run).write(text=_proposal(index)).version


def _increment_counter(args: tuple[str, int]) -> None:
    directory, count = args
    root = Path(directory)
    for _ in range(count):
        with path_lock(root / ".counter.lock"):
            with path_lock(root / ".counter.lock"):
                path = root / "counter.json"
                value = json.loads(path.read_text()) if path.exists() else {"count": 0}
                atomic_write_json(path, {"count": value["count"] + 1})


def test_process_lock_is_reentrant_and_does_not_lose_updates(tmp_path: Path) -> None:
    with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as workers:
        list(workers.map(_increment_counter, [(str(tmp_path), 8)] * 3))
    assert json.loads((tmp_path / "counter.json").read_text()) == {"count": 24}


def test_artifact_versions_are_unique_across_processes(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="parallel", project="pimc")
    with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as workers:
        versions = list(workers.map(_write_artifact, [(str(tmp_path), run.run_id, i) for i in range(9)]))
    assert {int(version[1:]) for version in versions} == set(range(1, 10))
    saved = [path.read_text() for path in run.subdir("idea").glob("idea_proposal.v*.md")]
    assert set(saved) == {_proposal(i) for i in range(9)}


def test_explicit_version_is_immutable_and_same_write_is_idempotent(tmp_path: Path) -> None:
    store = ArtifactStore(RunStore(tmp_path).create(task="immutable", project="pimc"))
    first = store.write(text=_proposal(1), version="v1")
    assert store.write(text=_proposal(1), version="v1") == first
    with pytest.raises(ArtifactConflictError):
        store.write(text=_proposal(2), version="v1")
    assert first.path.read_text() == _proposal(1)
    with pytest.raises(ValueError, match="use approve"):
        store.write(text=_proposal(2), version="approved")


def test_artifact_identity_preserves_original_line_endings(tmp_path: Path) -> None:
    store = ArtifactStore(RunStore(tmp_path).create(task="line-endings", project="pimc"))
    text = _proposal(1).replace("\n", "\r\n")
    source = store.write(text=text, version="v1")
    assert store.write(text=text, version="v1") == source
    approved = store.approve(source)
    assert source.path.read_bytes() == approved.path.read_bytes() == text.encode()
    source.path.write_bytes(text.replace("\r\n", "\n").encode())
    with pytest.raises(ArtifactCorruptionError, match="hash mismatch"):
        store.latest(agent_dir="idea", stem="idea_proposal")


def test_committed_approval_pointer_recovers_before_state_load(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="approval-recovery", project="pimc")
    store = ArtifactStore(run)
    first = store.write(text=_proposal(1))
    approved = store.approve(first)
    records = list((run.subdir("idea") / ".approvals/idea_proposal").glob("*.json"))
    assert len(records) == 1
    store.approve(first)
    assert len(list(records[0].parent.glob("*.json"))) == 1
    state = RunStateStore(run)
    state.write(graph=RunGraph(), request={}, status="waiting_review", expected_revision=0)
    approved.path.unlink()  # Actual damaged pointer, while committed source/receipt survive.
    assert state.load() is not None
    assert approved.path.read_text() == first.path.read_text()


def test_corrupt_approval_source_fails_closed(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="approval-corrupt", project="pimc")
    store = ArtifactStore(run)
    source = store.write(text=_proposal(1))
    approved = store.approve(source)
    before = approved.path.read_bytes()
    source.path.write_text(_proposal(2))
    with pytest.raises(ArtifactCorruptionError, match="hash mismatch"):
        store.latest(agent_dir="idea", stem="idea_proposal")
    assert approved.path.read_bytes() == before


def test_state_compare_and_swap_allows_exactly_one_concurrent_writer(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="cas", project="pimc")
    assert RunStateStore(run).write(graph=RunGraph(), request={}, status="created", expected_revision=0) == 1

    def update(index: int) -> str:
        try:
            RunStateStore(run).write(graph=RunGraph(), request={"writer": index}, status="created", expected_revision=1)
        except RunStateConflictError:
            return "conflict"
        return "committed"

    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(update, range(8)))
    assert results.count("committed") == 1
    assert results.count("conflict") == 7
    state = RunStateStore(run).load()
    assert state is not None and state.revision == 2


def test_run_creation_and_event_append_are_safe_under_fixed_clock(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    fixed = datetime(2026, 9, 17, tzinfo=timezone.utc)

    def create(index: int) -> str:
        run = store.create(task="same-task", project="pimc", now=fixed, user_request=str(index))
        return run.run_id

    with ThreadPoolExecutor(max_workers=8) as workers:
        ids = list(workers.map(create, range(16)))
    assert len(set(ids)) == len(store.list()) == 16
    run = store.get(ids[0])
    assert run is not None
    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(lambda i: run.write_event("concurrent", {"index": i}), range(32)))
    rows = [json.loads(line) for line in (run.subdir("events") / "concurrent.jsonl").read_text().splitlines()]
    assert {row["index"] for row in rows} == set(range(32))
