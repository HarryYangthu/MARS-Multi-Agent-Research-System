"""Actual owned async cleanup under real SQLite failures, never Agent results."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from filelock import FileLock

from app.bridge.agent_registry import AgentRegistry
from app.bridge.orchestrator import Orchestrator, RunSession
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.state_machine import NodeState
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunStore


def _session(root: Path, *, orch: Orchestrator | None = None) -> tuple[Orchestrator, RunSession, RunStateStore]:
    run_store = RunStore(root)
    run = run_store.create(task="owned-stop-storage-contract", project="synthetic_regression", entrypoint="idea")
    graph = RunGraph()
    graph.add_node("idea")
    # Authored lifecycle input. No model, tool, or Agent is invoked by these tests.
    graph.restore_state("idea", NodeState.RUNNING)
    store = RunStateStore(run)
    store.write(graph=graph, request={"task": run.task, "project": run.project,
        "entrypoint": "idea", "standalone": True}, status="running", expected_revision=0)
    orch = orch or Orchestrator(run_store=run_store, registry=AgentRegistry(), bus=InProcessEventBus())
    return orch, orch.session(run.run_id), store


def _refuse_update(store: RunStateStore, *, cleanup_only: bool = False) -> None:
    condition = "WHEN json_extract(NEW.payload, '$.termination.cleanup_complete') = 1 " if cleanup_only else ""
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("CREATE TRIGGER refuse_stop BEFORE UPDATE ON run_state " + condition
                           + "BEGIN SELECT RAISE(ABORT,'real storage fault'); END")


@pytest.mark.parametrize("failure", ["sqlite", "cas"])
@pytest.mark.asyncio
async def test_request_persistence_failure_still_cancels_and_cleans_owned_task(tmp_path: Path, failure: str) -> None:
    orch, session, store = _session(tmp_path)
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def owned_wait() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    assert orch._spawn_owned(session, "actual-async-cleanup", owned_wait)
    await started.wait()
    if failure == "sqlite":
        _refuse_update(store)
    else:
        snapshot = store.load()
        assert snapshot is not None
        store.write(graph=snapshot.graph, request=snapshot.request, status="running", expected_revision=snapshot.revision)
    revision = store.load()
    assert revision is not None
    result = await orch.stop_owned_run(session.run.run_id, grace_seconds=1)
    assert cleaned.is_set() and orch.owned_tasks.active(session.run.run_id) is None
    assert orch.owned_tasks.stopping(session.run.run_id) and session.read_only
    assert result["ok"] is False and result["status"] == "stop_state_error"
    assert result["state_persisted"] is False and result["owned_task_done"] is True
    assert result["state_persistence_error"]["phase"] == "request"
    assert result["state_persistence_error"]["type"] == ("RunStateIntegrityError" if failure == "sqlite" else "RunStateConflictError")
    assert result["termination"].get("cleanup_complete") is not True
    persisted = store.load()
    assert persisted and persisted.revision == revision.revision and persisted.termination is None
    assert persisted.graph.state("idea") == NodeState.RUNNING and store.pending_events() == []
    assert await orch.stop_owned_run(session.run.run_id) == result
    with FileLock(session.run.root / "runtime.driver.lock", timeout=0):
        pass  # Actual driver OS lock was released despite the persistence error.


@pytest.mark.asyncio
async def test_failed_stop_with_slow_cleanup_stays_incomplete_and_cancel_is_sent_once(tmp_path: Path) -> None:
    orch, session, store = _session(tmp_path)
    started, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))

    async def owned_wait() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            cleaned.set()

    assert orch._spawn_owned(session, "slow-actual-cleanup", owned_wait)
    await started.wait()
    _refuse_update(store)
    task = orch.owned_tasks.active(session.run.run_id)
    assert task is not None
    try:
        first = await orch.stop_owned_run(session.run.run_id, grace_seconds=0)
        await asyncio.wait_for(cleaning.wait(), timeout=1)
        second = await orch.stop_owned_run(session.run.run_id, grace_seconds=0)
        for result in (first, second):
            assert result["status"] == "stop_incomplete" and result["ok"] is False
            assert result["state_persisted"] is False and result["owned_task_done"] is False
            assert result["termination"].get("cleanup_complete") is not True
        assert session.read_only and task.cancelling() == 1 and not cleaned.is_set()
    finally:
        release.set()
        assert await orch.owned_tasks.wait(task, timeout=1)
    result = await orch.stop_owned_run(session.run.run_id)
    assert cleaned.is_set() and result["status"] == "stop_state_error" and result["owned_task_done"] is True


@pytest.mark.parametrize("failure", ["sqlite", "missing_db"])
@pytest.mark.asyncio
async def test_cleanup_persistence_failure_does_not_claim_durable_completion(tmp_path: Path, failure: str) -> None:
    orch, session, store = _session(tmp_path)
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def owned_wait() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            if failure == "missing_db":
                store.database_path.unlink()
            cleaned.set()

    assert orch._spawn_owned(session, "actual-cleanup-commit-fault", owned_wait)
    await started.wait()
    if failure == "sqlite":
        _refuse_update(store, cleanup_only=True)
    result = await orch.stop_owned_run(session.run.run_id, grace_seconds=1)
    assert cleaned.is_set() and result["owned_task_done"] is True
    assert result["ok"] is False and result["status"] == "stop_state_error" and result["state_persisted"] is False
    assert result["state_persistence_error"]["phase"] == "cleanup"
    assert result["termination"]["cleanup_complete"] is False
    assert session.graph.state("idea") == NodeState.RUNNING and session.read_only
    assert not (session.run.root / "events/run_lifecycle.jsonl").exists()
    if failure == "sqlite":
        snapshot = store.load()
        assert snapshot and snapshot.status == "cancelling" and snapshot.revision == 2
        assert snapshot.termination and snapshot.termination["cleanup_complete"] is False
        assert store.pending_events() == []
    with FileLock(session.run.root / "runtime.driver.lock", timeout=0):
        pass


@pytest.mark.asyncio
async def test_shutdown_continues_cancelling_other_owned_tasks_after_storage_failure(tmp_path: Path) -> None:
    orch, broken, store = _session(tmp_path)
    _, healthy, _ = _session(tmp_path, orch=orch)
    started = [asyncio.Event(), asyncio.Event()]
    cleaned = [asyncio.Event(), asyncio.Event()]

    async def owned_wait(index: int) -> None:
        started[index].set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned[index].set()

    assert orch._spawn_owned(broken, "broken-run-actual-cleanup", lambda: owned_wait(0))
    assert orch._spawn_owned(healthy, "healthy-run-actual-cleanup", lambda: owned_wait(1))
    await asyncio.gather(*(event.wait() for event in started))
    _refuse_update(store)
    results = await orch.shutdown_owned_runs(grace_seconds=1)
    by_run = {result["run_id"]: result for result in results}
    assert all(event.is_set() for event in cleaned) and orch.owned_tasks.run_ids() == ()
    assert by_run[broken.run.run_id]["status"] == "stop_state_error"
    assert by_run[broken.run.run_id]["ok"] is False
    assert by_run[healthy.run.run_id]["status"] == "stopped" and by_run[healthy.run.run_id]["ok"] is True
