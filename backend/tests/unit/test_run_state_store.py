from __future__ import annotations

from pathlib import Path

import pytest

from app.bridge.agent_registry import AgentRegistry
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunStore
from app.storage.run_state_store import RunStateStore


def _proposal_text() -> str:
    return fm_dumps(
        {
            "schema": "proposal.v1",
            "project": "pimc",
            "agent": "idea",
            "research_question": "How to simplify the router?",
            "hypothesis": "Hard top-2 keeps RES within 1.5 dB.",
            "novelty": "Stream-aware routing absent in surveys.",
        },
        "Body of proposal\n",
    )


def test_run_state_written_and_recovered(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    orch = Orchestrator(run_store=store)
    session = orch.create_session(
        RunRequest(task="state", project="pimc", auto_approve=True)
    )
    session.graph.transition("idea", NodeState.RUNNING)
    orch._persist_state(session, status="running")  # noqa: SLF001

    snapshot = RunStateStore(session.run).load()
    assert snapshot is not None
    assert snapshot.graph.state("idea") == NodeState.RUNNING

    recovered = Orchestrator(run_store=store).session(session.run.run_id)
    assert recovered.graph.state("idea") == NodeState.RUNNING
    assert recovered.request.project == "pimc"


@pytest.mark.asyncio
async def test_inline_approval_recovers_waiting_review_state(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path)
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=store, registry=AgentRegistry(), bus=bus)
    session = orch.create_session(
        RunRequest(
            task="inline-approval",
            project="pimc",
            entrypoint="idea",
            standalone=True,
            auto_approve=False,
        )
    )
    ref = ArtifactStore(session.run).write(text=_proposal_text())
    ArtifactStore(session.run).approve(ref)
    session.graph.transition("idea", NodeState.RUNNING)
    session.graph.transition("idea", NodeState.WAITING_REVIEW)
    orch._persist_state(session, status="running")  # noqa: SLF001

    recovered = Orchestrator(run_store=store, registry=AgentRegistry(), bus=bus)
    result = await recovered.resume_after_artifact_approval(
        run_id=session.run.run_id,
        agent="idea",
    )

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert recovered.session(session.run.run_id).graph.state("idea") == NodeState.DONE
    states = recovered.session(session.run.run_id).graph.all_states()
    assert all(state == NodeState.DONE for state in states.values())
    snapshot = RunStateStore(session.run).load()
    assert snapshot is not None
    assert snapshot.status == "completed"
