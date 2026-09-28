"""Real SQLite/process tests of state persistence; no agent/provider execution."""
from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import socket
import sqlite3
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import pytest
from filelock import FileLock

from app.bridge.agent_registry import AgentRegistry
from app.bridge.orchestrator import Orchestrator
from app.harness.runtime.event_bus import InProcessEventBus, RedisEventBus
from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.state_journal import StateJournal
from app.harness.runtime.state_machine import NodeState
from app.harness.schema.frontmatter_parser import dumps
from app.harness.schema.validator import validate_document
from app.storage.artifact_store import ArtifactStore
from app.storage.run_state_store import (
    RunStateConflictError, RunStateIntegrityError, RunStateMigrationRequired, RunStateStore,
)
from app.storage.run_store import RunHandle, RunStore


def _run(tmp_path: Path) -> tuple[RunHandle, RunStateStore, RunGraph]:
    run = RunStore(tmp_path).create(task="journal-contract", project="pimc", entrypoint="idea")
    graph = RunGraph()
    graph.add_node("idea")
    graph.set_entrypoint("idea")
    store = RunStateStore(run)
    store.write(graph=graph, request={"task": run.task, "project": run.project, "entrypoint": "idea",
                "standalone": True}, status="created", expected_revision=0)
    return run, store, graph


def _journal(store: RunStateStore) -> StateJournal:
    marker = json.loads(store.authority_path.read_text())
    return StateJournal(store.database_path, run_id=store.run.run_id, journal_id=marker["journal_id"])


def _legacy(tmp_path: Path, *, state: str = "pending") -> tuple[RunHandle, RunStateStore, bytes]:
    run = RunStore(tmp_path).create(task="legacy-contract", project="pimc", entrypoint="idea")
    graph = RunGraph()
    graph.add_node("idea")
    graph.restore_state("idea", NodeState(state))
    payload = {"schema": "run_state.v1", "run_id": run.run_id, "task": run.task, "project": run.project,
               "entrypoint": run.entrypoint, "status": "created", "updated_at": "2026-09-28T00:00:00+00:00",
               "revision": 4, "request": {"task": run.task, "project": run.project, "entrypoint": "idea"},
               "graph": graph.to_dict()}
    store = RunStateStore(run)
    source = json.dumps(payload).encode()
    store.path.write_bytes(source)
    return run, store, source


def _race(path: str, run_id: str, journal_id: str, payload: dict[str, Any], barrier: Any, queue: Any) -> None:
    journal = StateJournal(Path(path), run_id=run_id, journal_id=journal_id)
    barrier.wait(timeout=10)
    try:
        journal.commit(payload, expected_revision=1)
        queue.put("committed")
    except RunStateConflictError:
        queue.put("conflict")


def _interruptible(path: str, run_id: str, journal_id: str, payload: dict[str, Any],
                   after_commit: bool, pipe: Connection) -> None:
    journal = StateJournal(Path(path), run_id=run_id, journal_id=journal_id)
    if after_commit:
        journal.commit(payload, expected_revision=1)
        pipe.send("committed")
        pipe.recv()
    else:
        with journal.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            payload["revision"] = 2
            # Same real tables and transaction boundary, paused before COMMIT.
            connection.execute("UPDATE run_state SET revision=2,payload=? WHERE id=1", (json.dumps(payload),))
            connection.execute("INSERT INTO state_events(event_id,revision,payload) VALUES ('uncommitted',2,'{}')")
            pipe.send("staged")
            pipe.recv()


def test_state_and_event_commit_together_and_json_is_projection(tmp_path: Path) -> None:
    _, store, graph = _run(tmp_path)
    graph.transition("idea", NodeState.RUNNING)
    assert store.write(graph=graph, request={}, status="running", expected_revision=1) == 2
    with sqlite3.connect(store.database_path) as connection:
        revision, payload = connection.execute("SELECT revision,payload FROM run_state").fetchone()
        event_revision, event = connection.execute("SELECT revision,payload FROM state_events").fetchone()
    assert revision == event_revision == 2
    assert json.loads(payload)["graph"]["nodes"][0]["state"] == "running"
    assert json.loads(event)["from_state"] == "pending"
    assert json.loads(event)["to_state"] == "running"
    store.path.write_text("invalid projection")
    snapshot = store.load()
    assert snapshot and snapshot.graph.state("idea") == NodeState.RUNNING


@pytest.mark.asyncio
async def test_failed_transaction_does_not_mutate_memory_or_publish(tmp_path: Path) -> None:
    run, store, _ = _run(tmp_path)
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=RunStore(tmp_path), registry=AgentRegistry(), bus=bus)
    session = orch.session(run.run_id)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("CREATE TRIGGER stop_write BEFORE UPDATE ON run_state BEGIN SELECT RAISE(ABORT,'storage fault'); END")
    async with bus.subscribe("*") as queue:
        with pytest.raises(RunStateIntegrityError):
            await orch._transition(session, "idea", NodeState.RUNNING)
        assert queue.empty()
    assert session.graph.state("idea") == NodeState.PENDING
    assert session.read_only
    assert _journal(store).read()["revision"] == 1
    assert store.pending_events() == []
    assert not (run.root / "events/agent_events.jsonl").exists()


@pytest.mark.asyncio
async def test_cas_loser_reloads_authority_before_any_publication(tmp_path: Path) -> None:
    run, store, graph = _run(tmp_path)
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=RunStore(tmp_path), registry=AgentRegistry(), bus=bus)
    stale = orch.session(run.run_id)
    graph.transition("idea", NodeState.FAILED)
    store.write(graph=graph, request={}, status="failed", expected_revision=1)
    async with bus.subscribe("*") as queue:
        with pytest.raises(RunStateConflictError):
            await orch._transition(stale, "idea", NodeState.RUNNING)
        assert queue.empty()
    assert stale.graph.state("idea") == NodeState.FAILED
    assert stale.state_revision == 2 and stale.read_only
    assert len(store.pending_events()) == 1


def test_two_real_processes_have_one_cas_winner(tmp_path: Path) -> None:
    _, store, _ = _run(tmp_path)
    journal = _journal(store)
    payload = journal.read()
    payload["graph"]["nodes"][0]["state"] = "running"
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = [context.Process(target=_race, args=(str(store.database_path), store.run.run_id,
                 journal.journal_id, payload, barrier, queue)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        assert sorted(queue.get(timeout=15) for _ in processes) == ["committed", "conflict"]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join(timeout=10)
        queue.close()
    assert journal.read()["revision"] == 2
    assert len(store.pending_events()) == 1


@pytest.mark.parametrize("after_commit", [False, True])
@pytest.mark.asyncio
async def test_process_death_before_or_after_commit_is_recoverable(tmp_path: Path, after_commit: bool) -> None:
    run, store, _ = _run(tmp_path)
    journal = _journal(store)
    payload = journal.read()
    payload["graph"]["nodes"][0]["state"] = "running"
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_interruptible, args=(str(store.database_path), run.run_id,
                 journal.journal_id, payload, after_commit, child))
    try:
        process.start()
        assert parent.poll(15)
        assert parent.recv() == ("committed" if after_commit else "staged")
        process.kill()
        process.join(timeout=10)
        assert process.exitcode is not None and process.exitcode != 0
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=10)
        parent.close()
        child.close()
    snapshot = store.load()
    assert snapshot and snapshot.revision == (2 if after_commit else 1)
    assert snapshot.graph.state("idea") == (NodeState.RUNNING if after_commit else NodeState.PENDING)
    if after_commit:
        persisted_event = store.pending_events()[0]
        bus = InProcessEventBus()
        recovered = Orchestrator(run_store=RunStore(tmp_path), registry=AgentRegistry(), bus=bus)
        async with bus.subscribe(f"run.{run.run_id}.agent_state") as queue:
            assert await recovered.replay_state_events(run.run_id) == 1
            event = queue.get_nowait()
            assert event.payload["event_id"] == persisted_event["event_id"]
            assert event.payload["revision"] == snapshot.revision
        assert store.pending_events() == []
    else:
        assert store.pending_events() == []


@pytest.mark.asyncio
async def test_real_bus_refusal_keeps_committed_outbox_for_restart(tmp_path: Path) -> None:
    run, store, _ = _run(tmp_path)
    # An actual bound, non-listening socket makes the local Redis connection fail.
    with socket.socket() as socket_owner:
        socket_owner.bind(("127.0.0.1", 0))
        port = socket_owner.getsockname()[1]
        unavailable = RedisEventBus(f"redis://127.0.0.1:{port}/0?socket_connect_timeout=0.1&socket_timeout=0.1")
        orch = Orchestrator(run_store=RunStore(tmp_path), registry=AgentRegistry(), bus=unavailable)
        session = orch.session(run.run_id)
        await asyncio.wait_for(orch._transition(session, "idea", NodeState.RUNNING), timeout=5)
        await unavailable.close()
    pending = store.pending_events()
    assert session.graph.state("idea") == NodeState.RUNNING and len(pending) == 1
    bus = InProcessEventBus()
    fresh = Orchestrator(run_store=RunStore(tmp_path), registry=AgentRegistry(), bus=bus)
    async with bus.subscribe(f"run.{run.run_id}.agent_state") as queue:
        assert await fresh.replay_state_events(run.run_id) == 1
        assert queue.get_nowait().payload["event_id"] == pending[0]["event_id"]
        assert await fresh.replay_state_events(run.run_id) == 0
    projection = [json.loads(line) for line in (run.root / "events/agent_events.jsonl").read_text().splitlines()]
    assert len(projection) == 2 and projection[0]["event_id"] == projection[1]["event_id"]


@pytest.mark.parametrize("damage", ["missing", "corrupt", "missing_marker", "identity", "invalid_state"])
def test_authority_damage_fails_closed(tmp_path: Path, damage: str) -> None:
    _, store, _ = _run(tmp_path)
    if damage == "missing":
        store.database_path.unlink()
    elif damage == "corrupt":
        store.database_path.write_bytes(b"broken sqlite")
    elif damage == "missing_marker":
        store.authority_path.unlink()
    elif damage == "identity":
        with sqlite3.connect(store.database_path) as connection:
            connection.execute("UPDATE identity SET run_id='different-run'")
    else:
        with sqlite3.connect(store.database_path) as connection:
            raw = json.loads(connection.execute("SELECT payload FROM run_state").fetchone()[0])
            raw["graph"]["nodes"][0]["state"] = "corrupted-state"
            connection.execute("UPDATE run_state SET payload=?", (json.dumps(raw),))
    with pytest.raises(RunStateIntegrityError):
        store.load()
    with pytest.raises((RunStateIntegrityError, RunStateMigrationRequired)):
        store.migrate_legacy()


def test_migration_is_explicit_preserves_source_and_budget(tmp_path: Path) -> None:
    run, store, source = _legacy(tmp_path)
    snapshot = store.load()
    assert snapshot and snapshot.migration_required
    with pytest.raises(RunStateMigrationRequired):
        store.write(graph=snapshot.graph, request={}, status="running", expected_revision=4)
    budget = run.root / "resources/model_budget.v1.json"
    budget.parent.mkdir(exist_ok=True)
    budget.write_bytes(b'{"authored_ledger_input":"must remain byte-identical"}')
    previous = budget.read_bytes()
    migrated = store.migrate_legacy()
    assert migrated.revision == 4 and not migrated.migration_required
    assert (run.root / "run_state.legacy.json").read_bytes() == source
    assert json.loads(store.authority_path.read_text())["migration"]["sha256"] == hashlib.sha256(source).hexdigest()
    assert budget.read_bytes() == previous
    assert store.pending_events() == []  # migration never manufactures prior transitions


@pytest.mark.parametrize("lease", ["runtime.driver.lock", "resources/requests/active.lock"])
def test_migration_refuses_actual_owner_lock(tmp_path: Path, lease: str) -> None:
    run, store, source = _legacy(tmp_path)
    path = run.root / lease
    path.parent.mkdir(exist_ok=True, parents=True)
    with FileLock(path, timeout=0):
        with pytest.raises(RunStateMigrationRequired, match="active"):
            store.migrate_legacy()
    assert store.path.read_bytes() == source and not store.database_path.exists()


@pytest.mark.parametrize("state", ["waiting_review", "approved", "done"])
def test_legacy_progress_without_required_artifact_cannot_migrate(tmp_path: Path, state: str) -> None:
    _, store, source = _legacy(tmp_path, state=state)
    with pytest.raises(RunStateMigrationRequired, match="artifact"):
        store.migrate_legacy()
    assert store.path.read_bytes() == source and not store.authority_path.exists()


@pytest.mark.asyncio
async def test_ack_failure_replays_same_event_identity(tmp_path: Path) -> None:
    run, store, _ = _run(tmp_path)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("CREATE TRIGGER stop_ack BEFORE UPDATE ON state_events BEGIN SELECT RAISE(ABORT,'ack fault'); END")
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=RunStore(tmp_path), registry=AgentRegistry(), bus=bus)
    async with bus.subscribe(f"run.{run.run_id}.agent_state") as queue:
        await orch._transition(orch.session(run.run_id), "idea", NodeState.RUNNING)
        first = queue.get_nowait().payload
        assert store.pending_events()[0]["event_id"] == first["event_id"]
        assert _journal(store).read()["revision"] == first["revision"] == 2
        with sqlite3.connect(store.database_path) as connection:
            connection.execute("DROP TRIGGER stop_ack")
        assert await orch.replay_state_events(run.run_id) == 1
        assert queue.get_nowait().payload == first
    assert store.pending_events() == []


@pytest.mark.asyncio
async def test_projection_write_failure_does_not_undo_committed_state(tmp_path: Path) -> None:
    run, store, _ = _run(tmp_path)
    store.path.unlink()
    store.path.mkdir()  # Real filesystem error while replacing only the projection.
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=RunStore(tmp_path), registry=AgentRegistry(), bus=bus)
    session = orch.session(run.run_id)
    async with bus.subscribe(f"run.{run.run_id}.agent_state") as queue:
        await orch._transition(session, "idea", NodeState.RUNNING)
        assert queue.get_nowait().payload["revision"] == 2
    assert session.graph.state("idea") == NodeState.RUNNING and not session.read_only
    snapshot = store.load()
    assert snapshot and snapshot.graph.state("idea") == NodeState.RUNNING
    assert store.pending_events() == []


def test_sqlite_projection_cannot_be_reimported_after_authority_loss(tmp_path: Path) -> None:
    _, store, _ = _run(tmp_path)
    store.database_path.unlink()
    store.authority_path.unlink()
    with pytest.raises(RunStateIntegrityError, match="authority is missing"):
        store.load()
    with pytest.raises(RunStateIntegrityError, match="authority is missing"):
        store.migrate_legacy()
    assert not store.database_path.exists()


def test_real_relative_root_and_legacy_crlf_are_preserved(tmp_path: Path) -> None:
    import os
    relative = Path(os.path.relpath(tmp_path, Path.cwd()))
    _, store, graph = _run(relative / "fresh")
    graph.transition("idea", NodeState.RUNNING)
    assert store.write(graph=graph, request={}, status="running", expected_revision=1) == 2
    assert store.load() is not None
    run, legacy, source = _legacy(relative / "legacy")
    source += b"\r\n"
    legacy.path.write_bytes(source)
    legacy.migrate_legacy()
    assert (run.root / "run_state.legacy.json").read_bytes() == source
    assert json.loads(legacy.authority_path.read_text())["migration"]["sha256"] == hashlib.sha256(source).hexdigest()


@pytest.mark.parametrize("state", ["waiting_review", "approved", "done"])
def test_legacy_migration_rejects_schema_valid_artifact_from_another_project(tmp_path: Path, state: str) -> None:
    run, store, source = _legacy(tmp_path, state=state)
    document = dumps({"schema": "proposal.v1", "project": "another_project", "agent": "idea",
        "research_question": "Does the archived document belong to this project?",
        "hypothesis": "A different project is not valid evidence for this run.",
        "novelty": "Manually authored identity-check input, not an execution result."}, "Historical human-authored input.")
    assert validate_document(document, expected_schema="proposal.v1").valid
    artifacts = ArtifactStore(run)
    draft = artifacts.write(text=document)
    if state != "waiting_review":
        artifacts.approve(draft)
    budget = run.root / "resources/model_budget.v1.json"
    budget.parent.mkdir(exist_ok=True)
    budget.write_bytes(b'{"authored_ledger_input":"must stay unchanged"}\r\n')
    original_budget = budget.read_bytes()
    original_artifacts = {path: path.read_bytes() for path in run.subdir("idea").rglob("*") if path.is_file()}
    with pytest.raises(RunStateMigrationRequired, match="this run/project"):
        store.migrate_legacy()
    assert store.path.read_bytes() == source and budget.read_bytes() == original_budget
    assert not store.database_path.exists() and not store.authority_path.exists()
    assert not (run.root / "run_state.legacy.json").exists()
    assert all(path.read_bytes() == data for path, data in original_artifacts.items())


@pytest.mark.parametrize("matches", [False, True])
def test_legacy_migration_binds_schema_defined_execution_run_identity(tmp_path: Path, matches: bool) -> None:
    run = RunStore(tmp_path).create(task="execution-identity-contract", project="synthetic_regression", entrypoint="execution")
    graph = RunGraph()
    graph.add_node("execution")
    graph.set_entrypoint("execution")
    graph.restore_state("execution", NodeState.WAITING_REVIEW)
    store = RunStateStore(run)
    source = json.dumps({"schema": "run_state.v1", "run_id": run.run_id, "project": run.project,
        "task": run.task, "entrypoint": run.entrypoint, "status": "waiting_review", "updated_at": run.created_at,
        "revision": 1, "request": {"task": run.task, "project": run.project, "entrypoint": run.entrypoint},
        "graph": graph.to_dict()}).encode()
    store.path.write_bytes(source)
    document = dumps({"schema": "run_log.v1", "project": run.project, "agent": "execution",
        "run_id": run.run_id if matches else "another-run", "status": "interrupted",
        "metrics": {"planned_experiments": 1}, "fingerprint_hash": "sha256:" + hashlib.sha256(source).hexdigest()},
        "Human-authored pending plan for identity validation; no experiment ran.")
    assert validate_document(document, expected_schema="run_log.v1").valid
    ArtifactStore(run).write(text=document)
    if matches:
        snapshot = store.migrate_legacy()
        assert not snapshot.migration_required and snapshot.graph.state("execution") == NodeState.WAITING_REVIEW
        assert (run.root / "run_state.legacy.json").read_bytes() == source
    else:
        with pytest.raises(RunStateMigrationRequired, match="this run/project"):
            store.migrate_legacy()
        assert store.path.read_bytes() == source and not store.authority_path.exists()


@pytest.mark.parametrize("linked", ["external_artifact", "internal_artifact", "run_directory", "stage_directory", "approval_directory", "other_approval_directory",
                                    "source_state", "authority_target", "backup_target", "driver_lock"])
def test_legacy_migration_refuses_linked_paths_before_approval_recovery_or_writes(tmp_path: Path, linked: str) -> None:
    run, store, source = _legacy(tmp_path / "runs", state="done")
    document = dumps({"schema": "proposal.v1", "project": run.project, "agent": "idea",
        "research_question": "Are migration inputs owned by this run?",
        "hypothesis": "External mutable paths cannot become trusted state.",
        "novelty": "Human-authored migration input; no research execution."}, "Archival ownership check.")
    artifacts = ArtifactStore(run)
    approved = artifacts.approve(artifacts.write(text=document))
    external = tmp_path / "outside"
    external.mkdir()
    target = external / "external.md"
    target.write_text(document)
    if linked in {"external_artifact", "internal_artifact"}:
        if linked == "internal_artifact":
            target = run.subdir("context") / "other-artifact.md"
            target.write_text(document)
        approved.path.unlink()
        approved.path.symlink_to(target)
    elif linked == "run_directory":
        displaced = external / "linked-run"
        run.root.rename(displaced)
        run.root.symlink_to(displaced, target_is_directory=True)
    elif linked == "stage_directory":
        displaced = external / "idea"
        run.subdir("idea").rename(displaced)
        run.subdir("idea").symlink_to(displaced, target_is_directory=True)
        # Recovery would overwrite this outside pointer from its valid receipt.
        (displaced / approved.filename).write_text("must not be repaired outside the run")
    elif linked == "approval_directory":
        approval_root = run.subdir("idea") / ".approvals"
        displaced = external / "approvals"
        approval_root.rename(displaced)
        approval_root.symlink_to(displaced, target_is_directory=True)
    elif linked == "other_approval_directory":
        (run.subdir("idea") / ".approvals" / "legacy_custom_stem").symlink_to(external, target_is_directory=True)
    else:
        path = {"source_state": store.path, "authority_target": store.authority_path,
                "backup_target": run.root / "run_state.legacy.json", "driver_lock": run.root / "runtime.driver.lock"}[linked]
        target = external / "linked-state-or-lock"
        target.write_bytes(source if linked == "source_state" else b"must not be read, replaced, or truncated")
        path.unlink(missing_ok=True)
        path.symlink_to(target)
    budget = run.root / "resources/model_budget.v1.json"
    budget.parent.mkdir(exist_ok=True)
    budget.write_bytes(b'{"authored_ledger_input":"preserve this ledger"}\r\n')
    budget_bytes = budget.read_bytes()
    external_bytes = {path: path.read_bytes() for path in external.rglob("*") if path.is_file()}
    with pytest.raises(RunStateMigrationRequired, match="symbolic links"):
        store.migrate_legacy()
    assert store.path.read_bytes() == source and budget.read_bytes() == budget_bytes
    assert not store.database_path.exists()
    assert all(path.read_bytes() == data for path, data in external_bytes.items())
    if linked != "authority_target":
        assert not store.authority_path.exists()
    if linked != "backup_target":
        assert not (run.root / "run_state.legacy.json").exists()
