"""Phase 4 e2e: orchestrator parks at WAITING_REVIEW, REST API drives approval."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.bridge.agent_registry import get_registry, reset_registry_for_tests
from app.bridge.diagnostics import DiagnosticsConfig, MetricRule
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.hitl.review_session import get_registry as get_review_registry, reset_registry_for_tests as reset_review
from app.storage.run_store import RunStore


@pytest.fixture(autouse=True)
def _setup(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for env in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "QWEN_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("MARS_MOCK_MODE", "always")
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
async def test_pipeline_pauses_for_feedback_when_metrics_miss_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.bridge import commander_agent as commander_mod

    def _failing_diagnostics(project: str) -> DiagnosticsConfig:
        return DiagnosticsConfig(
            project=project,
            max_iterations=2,
            allowed_targets=("coding", "experiment"),
            default_target="coding",
            analyzers={
                "metrics_gap": True,
                "config_sanity": False,
                "code_change_risk": False,
            },
            metric_rules=(
                MetricRule(
                    name="loss",
                    target=0.0,
                    direction="lte",
                    aggregation="max",
                ),
            ),
        )

    monkeypatch.setattr(commander_mod, "load_diagnostics_config", _failing_diagnostics)

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

    # Drive review approvals one by one as they appear. After Execution, the
    # Commander/Bridge writes failure analysis and pauses for feedback-loop
    # approval instead of letting Writing run immediately.
    approved = []
    for _ in range(200):
        if session.waiting_for_feedback:
            break
        await asyncio.sleep(0.1)
        for agent_name in ("idea", "experiment", "coding", "execution", "writing"):
            review = get_review_registry().get(session.run.run_id, agent_name)
            if review and review.decision is None:
                review.approve()
                approved.append(agent_name)
    await asyncio.wait_for(run_task, timeout=10.0)

    states = session.graph.all_states()
    assert states["idea"] == NodeState.DONE
    assert states["experiment"] == NodeState.DONE
    assert states["coding"] == NodeState.DONE
    assert states["execution"] == NodeState.DONE
    assert states["writing"] == NodeState.PENDING
    assert session.waiting_for_feedback
    assert {"idea", "experiment", "coding", "execution"}.issubset(set(approved))
    assert (session.run.subdir("diagnosis") / "diagnosis.v1.md").exists()

    # Audit trail recorded approvals.
    audit_path = session.run.subdir("hitl") / "review_log.jsonl"
    assert audit_path.exists()
    log = audit_path.read_text(encoding="utf-8").strip().splitlines()
    actions = [line for line in log if '"approve"' in line]
    assert len(actions) >= 4


@pytest.mark.asyncio
async def test_reject_marks_run_failed(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=RunStore(tmp_path), bus=bus)
    session = orch.create_session(
        RunRequest(task="reject-flow", project="moe-pimc", auto_approve=False)
    )
    async with bus.subscribe(f"run.{session.run.run_id}.hitl") as queue:
        run_task = asyncio.create_task(orch.run(session.run.run_id))
        event = await asyncio.wait_for(queue.get(), timeout=10.0)

    payload = event.payload
    assert payload["event"] == "hitl.review_required"
    assert payload["agent"] == "idea"
    assert payload["evaluation_summary"]["artifact_ref"] == "idea/idea_proposal.v1.md"
    assert payload["evaluation_summary"]["report_count"] >= 1
    assert payload["review_priority"] in {"normal", "elevated", "high", "critical"}
    assert payload["recommended_action"]

    rejected = False
    for _ in range(80):
        await asyncio.sleep(0.1)
        review = get_review_registry().get(session.run.run_id, "idea")
        if review and review.decision is None:
            review.reject(reason="not novel")
            rejected = True
            break
    await asyncio.wait_for(run_task, timeout=10.0)

    assert rejected
    assert session.graph.state("idea") == NodeState.FAILED
