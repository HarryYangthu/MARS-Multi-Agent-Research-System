"""Human-authored artifacts exercise the real review queue, state and audit log."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
import pytest
from app.agents.idea.agent import IdeaAgent
from app.bridge.agent_registry import AgentRegistry
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.harness.schema.frontmatter_parser import dumps
from app.hitl.review_session import get_registry, reset_registry_for_tests
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunStore


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_human_document_review_updates_state_and_audit(tmp_path: Path, action: str) -> None:
    reset_registry_for_tests()
    registry = AgentRegistry()
    registry.register("idea", IdeaAgent())
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=RunStore(tmp_path), registry=registry, bus=bus)
    session = orch.create_session(RunRequest(task="human-review", project="pimc", auto_approve=False))
    document = dumps({"schema": "proposal.v1", "project": "pimc", "agent": "idea",
        "research_question": "Can a bounded intervention reduce validation error?",
        "hypothesis": "Moving interpolation nodes may reduce validation error.",
        "novelty": "A human-authored test proposal with no scientific claim."}, "Human-authored review input.")
    ArtifactStore(session.run).write(text=document)
    await orch._transition(session, "idea", NodeState.RUNNING)
    await orch._transition(session, "idea", NodeState.WAITING_REVIEW)
    async with bus.subscribe(f"run.{session.run.run_id}.hitl") as queue:
        task = asyncio.create_task(orch._await_hitl_or_auto(session, "idea"))
        try:
            event = await asyncio.wait_for(queue.get(), timeout=5)
            assert event.payload["event"] == "hitl.review_required"
            review = get_registry().get(session.run.run_id, "idea")
            assert review is not None
            if action == "approve":
                review.approve(actor="human-author")
            else:
                review.reject(actor="human-author", reason="Needs a concrete experiment")
            await asyncio.wait_for(task, timeout=5)
        finally:
            if not task.done():
                task.cancel()
            reset_registry_for_tests()
    assert session.graph.state("idea") == (NodeState.APPROVED if action == "approve" else NodeState.FAILED)
    rows = [json.loads(line) for line in (session.run.subdir("hitl") / "review_log.jsonl").read_text().splitlines()]
    assert any(row["action"] == action and row["actor"] == "human-author" for row in rows)
    approved = list(session.run.subdir("idea").glob("*.approved.md"))
    assert bool(approved) == (action == "approve")


@pytest.mark.asyncio
async def test_missing_agent_cannot_be_approved_at_review_boundary(tmp_path: Path) -> None:
    orch = Orchestrator(run_store=RunStore(tmp_path), registry=AgentRegistry(), bus=InProcessEventBus())
    session = orch.create_session(RunRequest(task="unconfigured-review", project="pimc", auto_approve=False))
    await orch._transition(session, "idea", NodeState.RUNNING)
    await orch._transition(session, "idea", NodeState.WAITING_REVIEW)
    await orch._await_hitl_or_auto(session, "idea")
    assert session.graph.state("idea") == NodeState.FAILED
