"""End-to-end orchestrator walk with no real Agents — Phase 2 e2e checkpoint."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.storage.run_store import RunStore


@pytest.mark.asyncio
async def test_pipeline_walks_all_nodes(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=RunStore(tmp_path), bus=bus)

    received: list[dict] = []  # type: ignore[type-arg]

    session = orch.create_session(
        RunRequest(
            task="phase2-dummy",
            project="moe-pimc",
            entrypoint="pipeline",
            auto_approve=True,
        )
    )

    # Subscribe FIRST, then trigger the run, so we don't race the publish.
    async with bus.subscribe("run.lifecycle") as q:
        run_task = asyncio.create_task(orch.run(session.run.run_id))
        try:
            while True:
                evt = await asyncio.wait_for(q.get(), timeout=2.0)
                received.append(evt.payload)
                if evt.payload.get("event") == "run.completed":
                    break
        except asyncio.TimeoutError:
            pass
        await run_task

    # Every node ended in DONE.
    assert all(s == NodeState.DONE for s in session.graph.all_states().values())

    # WS lifecycle events flowed.
    assert any(e.get("event") == "run.started" for e in received)
    assert any(e.get("event") == "run.completed" for e in received)

    # runs/<id>/events/agent_events.jsonl populated.
    log = (session.run.subdir("events") / "agent_events.jsonl").read_text().strip().splitlines()
    assert log, "agent_events.jsonl should have entries"
    parsed = [json.loads(line) for line in log]
    agents_seen = {p["agent"] for p in parsed}
    assert agents_seen == {"idea", "experiment", "coding", "execution", "writing"}


@pytest.mark.asyncio
async def test_pipeline_with_coding_entrypoint_skips_upstream(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=RunStore(tmp_path), bus=bus)
    session = orch.create_session(
        RunRequest(
            task="phase2-mid",
            project="moe-pimc",
            entrypoint="coding",
            auto_approve=True,
        )
    )
    await orch.run(session.run.run_id)
    states = session.graph.all_states()
    assert states["idea"] == NodeState.SKIPPED
    assert states["experiment"] == NodeState.SKIPPED
    assert states["coding"] == NodeState.DONE
    assert states["execution"] == NodeState.DONE
    assert states["writing"] == NodeState.DONE
