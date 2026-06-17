"""End-to-end orchestrator walk with no real Agents — Phase 2 e2e checkpoint."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.bridge.agent_registry import AgentRegistry
from app.bridge.diagnostics import load_diagnostics_config
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.storage.run_store import RunStore


@pytest.fixture(autouse=True)
def _mock_execution_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MARS_MOCK_MODE", "always")
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "mock")
    monkeypatch.setenv("LOCAL_VLLM_BASE_URL", "")

    import app.settings as settings_mod
    from app.bridge import commander_agent as commander_mod

    settings_mod._settings = None
    monkeypatch.setattr(
        commander_mod,
        "load_diagnostics_config",
        load_diagnostics_config,
    )
    yield
    settings_mod._settings = None


@pytest.mark.asyncio
async def test_pipeline_walks_all_nodes(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=RunStore(tmp_path), registry=AgentRegistry(), bus=bus)

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
        while not q.empty():
            received.append(q.get_nowait().payload)

    # Every node ended in DONE.
    assert all(s == NodeState.DONE for s in session.graph.all_states().values())

    # WS lifecycle events flowed.
    assert any(e.get("event") == "run.started" for e in received)
    assert any(e.get("event") == "run.completed" for e in received)

    # runs/<id>/events/agent_events.jsonl populated.
    log = (session.run.subdir("events") / "agent_events.jsonl").read_text().strip().splitlines()
    assert log, "agent_events.jsonl should have entries"
    parsed = [json.loads(line) for line in log]
    agents_seen = {str(p["agent"]) for p in parsed if "agent" in p}
    assert {"idea", "experiment", "coding", "execution", "writing"}.issubset(
        agents_seen
    )
    if "coding_attempt_2" in agents_seen:
        assert "execution_attempt_2" in agents_seen


@pytest.mark.asyncio
async def test_pipeline_with_coding_entrypoint_skips_upstream(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=RunStore(tmp_path), registry=AgentRegistry(), bus=bus)
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
