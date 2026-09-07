"""Public progress delivery uses actual files and the production in-process bus."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.agents.base import RunRequest
from app.agents.experiment.agent import ExperimentAgent
from app.bridge.agent_progress import agent_progress_payload, build_agent_progress_sink
from app.bridge.agent_runner import load_agent_handoff_context
from app.harness.observability.events import normalize_event
from app.harness.runtime.event_bus import InProcessEventBus
from app.storage.run_store import RunStore


def test_progress_preserves_public_message_and_host_run_identity() -> None:
    event = agent_progress_payload(
        run_id="actual-run", project="pimc", node_key="idea_attempt_2",
        timestamp="2026-09-07T00:00:00+00:00",
        payload={"kind": "action", "message": "检查论文中节点分配的方法。", "phase": "research",
                 "invocation": "invocation-a", "run_id": "wrong-run", "project": "other",
                 "agent": "coding", "api_key": "must-not-be-copied", "context": "private input",
                 "to_state": "done"},
    )
    assert event["event"] == "agent.progress"
    assert event["message"] == event["summary"] == "检查论文中节点分配的方法。"
    assert event["run_id"] == "actual-run"
    assert event["project"] == "pimc"
    assert event["agent"] == "idea" and event["node"] == "idea_attempt_2"
    assert event["invocation"] == "invocation-a"
    assert not {"api_key", "context", "to_state"}.intersection(event)


@pytest.mark.parametrize("payload", [
    {"kind": "action", "message": ""},
    {"kind": "action", "message": "   "},
    {"kind": [], "message": "Invalid kind"},
    {"kind": "experiment_succeeded", "message": "Invalid progress event"},
    {"kind": "action", "message": "Public text", "phase": {}},
    {"kind": "action", "message": "Public text", "invocation": 3},
])
def test_invalid_progress_is_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        agent_progress_payload(run_id="run", project="pimc", node_key="idea",
                               timestamp="2026-09-07T00:00:00+00:00", payload=payload)


@pytest.mark.asyncio
async def test_progress_is_persisted_and_delivered_without_state_transition(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="progress-transport", project="pimc", entrypoint="idea")
    bus = InProcessEventBus()
    channel = f"run.{run.run_id}.agent_state"
    sink = build_agent_progress_sink(run=run, node_key="idea", bus=bus)
    try:
        async with bus.subscribe(channel) as queue:
            await sink({"kind": "started", "message": "开始检查研究任务。", "phase": "understand"})
            await sink({"kind": "action", "message": "接下来查找相关论文。", "phase": "research"})
            received = [(await asyncio.wait_for(queue.get(), timeout=1)).payload for _ in range(2)]
        rows = [json.loads(line) for line in (run.subdir("events") / "agent_events.jsonl").read_text().splitlines()]
        assert [row["progress_seq"] for row in rows] == [1, 2]
        assert [row["message"] for row in rows] == [row["message"] for row in received]
        assert all(row["channel"] == channel and "to_state" not in row for row in rows)
        normalized = normalize_event(rows[-1], run_id=run.run_id, project=run.project,
                                     default_channel="agent", default_kind="agent.state_changed")
        assert normalized["kind"] == "agent.progress"
        assert normalized["payload"]["message"] == received[-1]["message"]
    finally:
        await bus.close()


@pytest.mark.asyncio
async def test_progress_can_be_replayed_without_a_live_bus(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="progress-file", project="pimc", entrypoint="idea")
    sink = build_agent_progress_sink(run=run, node_key="idea")
    await sink({"kind": "validation", "message": "正在检查方案交接字段。", "phase": "validate"})
    rows = (run.subdir("events") / "agent_events.jsonl").read_text().splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["kind"] == "validation"


@pytest.mark.asyncio
async def test_experiment_context_preserves_handoff_beyond_human_summary(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="human-handoff", project="pimc", entrypoint="experiment")
    # Human-authored input file tests transport, not proposal quality or Agent success.
    text = ("---\nschema: proposal.v1\nhuman_summary: 比较两种节点分配方法。\n"
            "handoff:\n  missing_context: [baseline_code, dataset]\n"
            "method_spec:\n  scope: method_proposal\n---\nHuman-authored transport input.\n")
    path = run.subdir("idea") / "idea_proposal.approved.md"
    path.write_text(text, encoding="utf-8")
    upstream, _ = load_agent_handoff_context(run, "experiment")
    assert text in upstream[path.name]
    agent = ExperimentAgent()
    request = RunRequest(project="pimc", user_request="设计实验", upstream_artifacts=upstream,
                         extra={"context_sources": {"project_rules": False, "code_repositories": False}})
    context = await agent.build_context(request)
    messages = agent._messages_for_context(request, context, purpose="handoff-contract")
    assert any(text in message.content for message in messages)
    assert "human_summary 仅用于快速概览" in context.system
    assert "不得猜测未提供的基线" in context.system
