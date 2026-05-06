"""Phase 4 e2e: orchestrator parks at WAITING_REVIEW, REST API drives approval."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.bridge.agent_registry import get_registry, reset_registry_for_tests
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.hitl.review_session import get_registry as get_review_registry, reset_registry_for_tests as reset_review
from app.storage.run_store import RunStore


@pytest.fixture(autouse=True)
def _setup(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "QWEN_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("LOCAL_VLLM_BASE_URL", "")
    import app.settings as settings_mod
    settings_mod._settings = None

    reset_registry_for_tests()
    reset_review()
    from app.agents.coding.agent import CodingAgent
    from app.agents.execution.agent import ExecutionAgent
    from app.agents.experiment.agent import ExperimentAgent
    from app.agents.idea.agent import IdeaAgent
    from app.agents.writing.agent import WritingAgent

    reg = get_registry()
    for cls in (IdeaAgent, ExperimentAgent, CodingAgent, ExecutionAgent, WritingAgent):
        agent = cls()
        reg.register(agent.name, agent)
    yield
    reset_registry_for_tests()
    reset_review()


@pytest.mark.asyncio
async def test_pipeline_parks_at_waiting_review(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=RunStore(tmp_path), bus=bus)
    session = orch.create_session(
        RunRequest(
            task="phase4-hitl",
            project="moe-pimc",
            entrypoint="pipeline",
            user_request="test",
            auto_approve=False,
        )
    )

    run_task = asyncio.create_task(orch.run(session.run.run_id))

    # Drive review approvals one by one as they appear.
    approved = []
    for _ in range(40):
        if all(s == NodeState.DONE for s in session.graph.all_states().values()):
            break
        await asyncio.sleep(0.1)
        for agent_name in ("idea", "experiment", "coding", "execution", "writing"):
            review = get_review_registry().get(session.run.run_id, agent_name)
            if review and review.decision is None:
                review.approve()
                approved.append(agent_name)
    await run_task

    states = session.graph.all_states()
    assert all(s == NodeState.DONE for s in states.values()), states
    assert set(approved) == {"idea", "experiment", "coding", "execution", "writing"}

    # Audit trail recorded approvals.
    audit_path = session.run.subdir("hitl") / "review_log.jsonl"
    assert audit_path.exists()
    log = audit_path.read_text(encoding="utf-8").strip().splitlines()
    actions = [line for line in log if '"approve"' in line]
    assert len(actions) == 5


@pytest.mark.asyncio
async def test_reject_marks_run_failed(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=RunStore(tmp_path), bus=bus)
    session = orch.create_session(
        RunRequest(task="reject-flow", project="moe-pimc", auto_approve=False)
    )
    run_task = asyncio.create_task(orch.run(session.run.run_id))

    rejected = False
    for _ in range(40):
        await asyncio.sleep(0.1)
        review = get_review_registry().get(session.run.run_id, "idea")
        if review and review.decision is None:
            review.reject(reason="not novel")
            rejected = True
            break
    await run_task

    assert rejected
    assert session.graph.state("idea") == NodeState.FAILED
