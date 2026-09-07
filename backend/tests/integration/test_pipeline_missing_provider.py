"""A real agent configuration error cannot complete the pipeline."""
from dataclasses import replace
from pathlib import Path
import pytest
from app.agents.idea.agent import IdeaAgent
from app.bridge.agent_registry import AgentRegistry
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.llm.model_registry import get_agent_config
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunStore


@pytest.mark.asyncio
async def test_unconfigured_provider_stops_real_agent_pipeline(tmp_path: Path) -> None:
    registry = AgentRegistry()
    cfg = replace(get_agent_config("idea"), model_provider="unconfigured-provider", debate_enabled=False)
    registry.register("idea", IdeaAgent(agent_config=cfg))
    orch = Orchestrator(run_store=RunStore(tmp_path), registry=registry, bus=InProcessEventBus())
    session = orch.create_session(RunRequest(task="missing-provider", project="pimc", auto_approve=True))
    await orch.run(session.run.run_id)
    assert session.graph.state("idea") == NodeState.FAILED
    assert not list(session.run.root.rglob("*.approved.md"))
    snapshot = RunStateStore(session.run).load()
    assert snapshot is not None and snapshot.status == "failed"
    assert all(value == NodeState.PENDING for name, value in session.graph.all_states().items() if name != "idea")
