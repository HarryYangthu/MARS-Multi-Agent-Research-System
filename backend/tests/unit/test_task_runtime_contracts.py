"""Pure contracts, real persisted files and asyncio ownership; no model substitutes."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.bridge.owned_run_tasks import OwnedRunTasks
from app.bridge.task_runtime import admit_handoffs, bind_task, resumable_task
from app.bridge.workflow_service import ready_batch
from app.harness.agent_loop.trace import LoopTrace
from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.task_contract import HandoffBlockedError, HandoffPrerequisite, missing_prerequisites
from app.harness.schema.frontmatter_parser import dumps
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunStore


def prerequisite(kind: str) -> HandoffPrerequisite:
    return HandoffPrerequisite.model_validate({"kind": kind, "description": "Caller-specified context",
        "reason": "Required for the requested operation", "blocks_execution": True})


def test_planning_admission_and_execution_admission_are_distinct() -> None:
    required = [prerequisite("baseline_code"), prerequisite("data_description")]
    assert missing_prerequisites(required, stage="experiment", supplied_context={}) == []
    assert missing_prerequisites(required, stage="coding", supplied_context={}) == ["baseline_code"]
    assert missing_prerequisites(required, stage="execution", supplied_context={"baseline_code": "source"}) == ["data_description"]
    assert missing_prerequisites(required, stage="execution", supplied_context={
        "baseline_code": "source", "data_description": "caller-owned data description"}) == []
    with pytest.raises(ValidationError):
        HandoffPrerequisite.model_validate({**required[0].model_dump(), "blocks_execution": "false"})


def test_handoff_admission_reads_approved_source_and_records_missing_context(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="handoff-admission", project="pimc")
    text = dumps({"schema": "proposal.v1", "project": "pimc", "agent": "idea",
        "research_question": "Human-authored contract input", "hypothesis": "No model or experiment result claimed",
        "novelty": "Admission contract coverage", "handoff": {"version": "idea.handoff.v1",
            "target_agent": "experiment", "scope": "method_proposal", "next_step": "Prepare a controlled comparison",
            "changes": [{"target": "model", "operation": "modify", "spec_ref": "/method_spec/model", "preserve": []}],
            "verification_requirements": [{"id": "accuracy", "question": "Compare errors", "comparison": "baseline/candidate",
                "metric": "error", "decision_rule_ref": "/decision_rule"}],
            "required_context": [prerequisite("baseline_code").model_dump()]}},
        "Human-authored admission fixture; not an Agent output.")
    store = ArtifactStore(run)
    artifact = store.write(text=text)
    store.approve(artifact)
    assert not admit_handoffs(run, "experiment", supplied_context={})[0].missing_context
    with pytest.raises(HandoffBlockedError, match="baseline_code"):
        admit_handoffs(run, "execution", supplied_context={})
    receipt = json.loads((run.root / "input/handoffs/execution.json").read_text())
    assert receipt["handoffs"][0]["missing_context"] == ["baseline_code"]
    accepted = admit_handoffs(run, "execution", supplied_context={"baseline_code": "provided source text"})
    assert accepted[0].source_ref == "idea/idea_proposal.approved.md"
    assert not accepted[0].missing_context


def test_task_binding_preserves_invocation_and_rejects_changed_inputs(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="task-binding", project="pimc")
    task = bind_task(run, "idea", goal="research", upstream={"background": "user text"}, output_schema="proposal.v1")
    resumed = bind_task(run, "idea", goal="research", upstream={"background": "user text"},
                        output_schema="proposal.v1", resume_invocation=task.invocation_id)
    assert task == resumed and task.parent_task_id == run.run_id
    with pytest.raises(ValueError, match="inputs changed"):
        bind_task(run, "idea", goal="changed", upstream={"background": "user text"},
                  output_schema="proposal.v1", resume_invocation=task.invocation_id)


def test_resume_preflight_rejects_unknown_tool_outcome_before_journal_access(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="resume-contract", project="pimc")
    task = bind_task(run, "idea", goal="research", upstream={}, output_schema="proposal.v1")
    root = run.root / "agent_traces/idea" / task.invocation_id
    root.mkdir(parents=True)
    # Caller-authored unsafe state; no execution or success is represented.
    (root / "checkpoint.json").write_text(json.dumps({"status": "interrupted", "pending": "tool"}))
    with pytest.raises(ValueError, match="tool_outcome_unknown"):
        resumable_task(run, "idea")


def test_resume_preflight_accepts_consistent_no_operation_boundary_and_rejects_stale_journal(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="resume-boundary", project="pimc")
    task = bind_task(run, "idea", goal="research", upstream={}, output_schema="proposal.v1")
    root = run.root / "agent_traces/idea" / task.invocation_id
    trace = LoopTrace(root, "full")
    state: dict[str, Any] = {"fingerprint": "input-contract", "status": "interrupted", "pending": None,
        "counts": {}, "usage": {}, "usage_complete": True}
    trace.emit("started", {"fixture": "checkpoint structure before any operation"})
    trace.snapshot(state)
    assert resumable_task(run, "idea") == task
    trace.emit("uncommitted_event", {})
    with pytest.raises(ValueError, match="inconsistent"):
        resumable_task(run, "idea")


def test_ready_batch_obeys_dependencies_stage_conflicts_and_workspace_conflicts() -> None:
    graph = RunGraph()
    for key in ("idea", "idea_attempt_2", "coding", "execution", "writing"):
        graph.add_node(key)
    graph.add_edge("idea", "writing")
    assert ready_batch(graph, 8) == ["idea", "coding"]
    assert ready_batch(graph, 1) == ["idea"]
    with pytest.raises(ValueError):
        ready_batch(graph, 0)


@pytest.mark.asyncio
async def test_driver_file_lock_excludes_independent_owners(tmp_path: Path) -> None:
    first, second = OwnedRunTasks(), OwnedRunTasks()
    release = asyncio.Event()
    async def wait_for_release() -> None:
        await release.wait()
    lock_path = tmp_path / "driver.lock"
    assert first.spawn("run", "wait", wait_for_release, finished=lambda: None, lock_path=lock_path)
    assert not second.spawn("run", "wait", wait_for_release, finished=lambda: None, lock_path=lock_path)
    running = first.active("run")
    assert running is not None
    release.set()
    assert await first.wait(running, timeout=1)
    assert second.spawn("run", "wait", wait_for_release, finished=lambda: None, lock_path=lock_path)
    other = second.active("run")
    assert other is not None and await second.wait(other, timeout=1)
