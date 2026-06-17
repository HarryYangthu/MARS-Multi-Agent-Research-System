from __future__ import annotations

from pathlib import Path

from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.runtime.state_machine import NodeState
from app.storage.run_store import RunStore
from app.storage.run_state_store import RunStateStore


def test_run_state_written_and_recovered(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    orch = Orchestrator(run_store=store)
    session = orch.create_session(
        RunRequest(task="state", project="moe-pimc", auto_approve=True)
    )
    session.graph.transition("idea", NodeState.RUNNING)
    orch._persist_state(session, status="running")  # noqa: SLF001

    snapshot = RunStateStore(session.run).load()
    assert snapshot is not None
    assert snapshot.graph.state("idea") == NodeState.RUNNING

    recovered = Orchestrator(run_store=store).session(session.run.run_id)
    assert recovered.graph.state("idea") == NodeState.RUNNING
    assert recovered.request.project == "moe-pimc"
