"""Actual empty agent registry must fail without downstream success artifacts."""
from __future__ import annotations
from pathlib import Path
import pytest
from app.bridge.agent_registry import AgentRegistry
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunStore

@pytest.mark.asyncio
async def test_pipeline_failure_sets_failed_run_state_and_event(
    tmp_path: Path,
) -> None:
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=RunStore(tmp_path), registry=AgentRegistry(), bus=bus)
    session = orch.create_session(
        RunRequest(
            task="phase2-failure",
            project="pimc",
            entrypoint="pipeline",
            auto_approve=True,
        )
    )

    received: list[dict] = []  # type: ignore[type-arg]
    async with bus.subscribe("run.lifecycle") as queue:
        await orch.run(session.run.run_id)
        while not queue.empty():
            received.append(queue.get_nowait().payload)

    snapshot = RunStateStore(session.run).load()
    assert snapshot is not None
    assert snapshot.status == "failed"
    assert snapshot.failed_nodes == ("idea",)
    assert snapshot.failure_summary == "1 run node(s) failed"
    assert session.graph.state("idea") == NodeState.FAILED
    assert all(
        state == NodeState.PENDING
        for key, state in session.graph.all_states().items()
        if key != "idea"
    )
    assert any(event.get("event") == "run.failed" for event in received)
    assert not any(event.get("event") == "run.completed" for event in received)
    state_text = (session.run.root / "run_state.json").read_text(encoding="utf-8")
    assert "sensitive-provider-response" not in state_text

@pytest.mark.asyncio
async def test_pipeline_with_coding_entrypoint_skips_upstream(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=RunStore(tmp_path), registry=AgentRegistry(), bus=bus)
    session = orch.create_session(
        RunRequest(
            task="phase2-mid",
            project="pimc",
            entrypoint="coding",
            auto_approve=True,
        )
    )
    await orch.run(session.run.run_id)
    states = session.graph.all_states()
    assert states["idea"] == NodeState.SKIPPED
    assert states["experiment"] == NodeState.SKIPPED
    assert states["coding"] == NodeState.FAILED
    assert states["execution"] == NodeState.PENDING
    assert states["writing"] == NodeState.PENDING
