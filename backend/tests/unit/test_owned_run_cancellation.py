"""Real asyncio ownership and caller-authored states; no execution substitutes."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.agents.idea.agent import IdeaAgent
from app.agents.idea.research_cancellation import archive_research_cancellation
from app.bridge.agent_registry import AgentRegistry
from app.bridge.orchestrator import Orchestrator, RunRequest, RunSession
from app.bridge.owned_run_tasks import OwnedRunTasks
from app.harness.agent_loop import AgentLoopPolicy
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.harness.schema.frontmatter_parser import dumps
from app.hitl.review_session import get_registry, reset_registry_for_tests
from app.storage.artifact_store import ArtifactStore
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunStore


def _session(root: Path) -> tuple[Orchestrator, RunSession]:
    registry = AgentRegistry()
    registry.register("idea", IdeaAgent())
    orch = Orchestrator(run_store=RunStore(root), registry=registry, bus=InProcessEventBus())
    session = orch.create_session(RunRequest(task="owned-lifecycle-contract", project="pimc",
                                           entrypoint="idea", standalone=True, auto_approve=False))
    return orch, session


async def _wait(event: asyncio.Event) -> None:
    await event.wait()


@pytest.mark.asyncio
async def test_owner_isolation_duplicate_prevention_and_cancel_before_first_step() -> None:
    owners = OwnedRunTasks()
    first, second = asyncio.Event(), asyncio.Event()
    finished: list[str] = []
    assert owners.spawn("one", "start", lambda: _wait(first), finished=lambda: finished.append("one"))
    assert not owners.spawn("one", "start", lambda: _wait(second), finished=lambda: finished.append("duplicate"))
    assert owners.spawn("two", "start", lambda: _wait(second), finished=lambda: finished.append("two"))
    task = owners.cancel_once("one")
    assert task is not None and await owners.wait(task, timeout=1)
    assert finished == ["one"] and not owners.stopping("two")
    assert owners.active("two") is not None
    second.set()
    other = owners.active("two")
    assert other is not None and await owners.wait(other, timeout=1)
    assert finished == ["one", "two"]


@pytest.mark.asyncio
async def test_cancel_is_sent_once_and_timeout_does_not_cancel_cleanup() -> None:
    owners = OwnedRunTasks()
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    finished: list[str] = []
    cancellations = 0

    async def lifecycle_wait() -> None:
        nonlocal cancellations
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations += 1
            cleaning.set()
            await release.wait()
            raise

    owners.spawn("one", "synchronization", lifecycle_wait, finished=lambda: finished.append("done"))
    await started.wait()
    task = owners.cancel_once("one")
    assert task is not None
    await cleaning.wait()
    assert not await owners.wait(task, timeout=0)
    assert owners.cancel_once("one") is task
    assert task.cancelling() == cancellations == 1 and not finished
    # A disconnected stop waiter must not interrupt the target's cleanup either.
    waiter = asyncio.create_task(owners.wait(task, timeout=1))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert task.cancelling() == 1
    release.set()
    assert await owners.wait(task, timeout=1)
    assert finished == ["done"]


@pytest.mark.asyncio
async def test_api_owned_start_deduplicates_and_can_stop_before_execution(tmp_path: Path) -> None:
    orch, session = _session(tmp_path)
    run_id = session.run.run_id
    assert orch.start_owned_run(run_id)["status"] == "started"
    assert orch.start_owned_run(run_id)["status"] == "already_running"
    # No yield has occurred: no Agent/provider/tool code is entered.
    result = await orch.stop_owned_run(run_id)
    assert result["status"] == "stopped" and result["termination"]["cleanup_complete"]
    assert session.graph.state("idea") == NodeState.PENDING
    assert not (session.run.root / "agent_traces").exists()
    assert not list(session.run.subdir("idea").glob("*.md"))
    assert not orch.start_owned_run(run_id)["ok"]
    assert not (await orch.request_artifact_revision(run_id=run_id, agent="idea", reason="No replay"))["ok"]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [NodeState.RUNNING, NodeState.FAILED, NodeState.WAITING_REVIEW, NodeState.APPROVED, NodeState.DONE])
async def test_cancel_preserves_known_states_and_only_fails_running(tmp_path: Path, state: NodeState) -> None:
    orch, session = _session(tmp_path)
    # Caller-authored graph state, not a claimed Agent execution/result.
    session.graph.restore_state("idea", state)
    gate = asyncio.Event()
    assert orch._spawn_owned(session, "lifecycle-synchronization", lambda: _wait(gate))
    result = await orch.stop_owned_run(session.run.run_id)
    expected = NodeState.FAILED if state == NodeState.RUNNING else state
    assert session.graph.state("idea") == expected
    snapshot = RunStateStore(session.run).load()
    assert snapshot is not None and snapshot.graph.state("idea") == expected
    assert snapshot.termination == result["termination"]
    assert snapshot.termination and snapshot.termination["type"] == "cancelled"
    assert snapshot.status == ("failed" if expected == NodeState.FAILED else
                               "waiting_review" if expected == NodeState.WAITING_REVIEW else
                               "completed" if expected == NodeState.DONE else "stopped")
    assert result["termination"]["interrupted_nodes"] == (["idea"] if state == NodeState.RUNNING else [])
    before = (session.run.root / "run_state.json").read_bytes()
    assert (await orch.stop_owned_run(session.run.run_id))["status"] == "stopped"
    assert (session.run.root / "run_state.json").read_bytes() == before


@pytest.mark.asyncio
async def test_stop_incomplete_is_not_success_and_cleanup_later_commits(tmp_path: Path) -> None:
    orch, session = _session(tmp_path)
    session.graph.restore_state("idea", NodeState.RUNNING)
    started, release = asyncio.Event(), asyncio.Event()

    async def lifecycle_wait() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await release.wait()

    assert orch._spawn_owned(session, "lifecycle-synchronization", lifecycle_wait)
    await started.wait()
    result = await orch.stop_owned_run(session.run.run_id, grace_seconds=0)
    assert result["status"] == "stop_incomplete" and not result["termination"]["cleanup_complete"]
    assert session.graph.state("idea") == NodeState.RUNNING
    task = orch.owned_tasks.active(session.run.run_id)
    assert task is not None and task.cancelling() == 1
    release.set()
    assert await orch.owned_tasks.wait(task, timeout=1)
    assert session.graph.state("idea") == NodeState.FAILED
    assert session.termination and session.termination["cleanup_complete"]


@pytest.mark.asyncio
async def test_real_human_review_survives_owned_wait_cancellation(tmp_path: Path) -> None:
    reset_registry_for_tests()
    orch, session = _session(tmp_path)
    document = dumps({"schema": "proposal.v1", "project": "pimc", "agent": "idea",
        "research_question": "A human-authored review contract?", "hypothesis": "Not a scientific result.",
        "novelty": "Lifecycle contract only."}, "Human-authored input; not generated by an Agent.")
    artifact = ArtifactStore(session.run).write(text=document)
    session.graph.restore_state("idea", NodeState.WAITING_REVIEW)
    original = artifact.path.read_bytes()
    async with session.bus.subscribe(f"run.{session.run.run_id}.hitl") as queue:
        assert orch._spawn_owned(session, "human-review", lambda: orch._await_hitl_or_auto(session, "idea"))
        event = await asyncio.wait_for(queue.get(), timeout=2)
        assert event.payload["event"] == "hitl.review_required"
        review = get_registry().get(session.run.run_id, "idea")
        assert review is not None
        result = await orch.stop_owned_run(session.run.run_id)
    assert result["status"] == "stopped" and session.graph.state("idea") == NodeState.WAITING_REVIEW
    assert artifact.path.read_bytes() == original and get_registry().get(session.run.run_id, "idea") is review
    assert review.decision is None and not list(session.run.subdir("idea").glob("*.approved.md"))
    before = (session.run.root / "run_state.json").read_bytes()
    recovered = Orchestrator(run_store=RunStore(tmp_path)).session(session.run.run_id)
    assert recovered.graph.state("idea") == NodeState.WAITING_REVIEW and recovered.termination
    assert (session.run.root / "run_state.json").read_bytes() == before
    reset_registry_for_tests()


@pytest.mark.asyncio
async def test_late_cancel_marker_does_not_enter_human_wait_or_start_new_stage(tmp_path: Path) -> None:
    orch, session = _session(tmp_path)
    session.graph.restore_state("idea", NodeState.WAITING_REVIEW)
    session.termination = {"type": "cancelled", "reason": "caller-authored lifecycle contract"}
    await asyncio.wait_for(orch._await_hitl_or_auto(session, "idea"), timeout=1)
    await orch.run(session.run.run_id)
    assert session.graph.state("idea") == NodeState.WAITING_REVIEW
    assert not (session.run.root / "agent_traces").exists()


@pytest.mark.asyncio
async def test_shutdown_only_cancels_owned_work(tmp_path: Path) -> None:
    orch, session = _session(tmp_path)
    gate = asyncio.Event()
    outsider = asyncio.create_task(gate.wait())
    assert orch.start_owned_run(session.run.run_id)["ok"]
    results = await orch.shutdown_owned_runs()
    assert results[0]["status"] == "stopped" and not outsider.done()
    assert session.graph.state("idea") == NodeState.PENDING
    assert not (session.run.root / "agent_traces").exists()
    assert orch.owned_tasks.closing
    gate.set()
    await outsider


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True])
async def test_explicit_human_approval_after_stop_keeps_artifact_and_history(tmp_path: Path, restart: bool) -> None:
    orch, session = _session(tmp_path)
    store = ArtifactStore(session.run)
    artifact = store.write(text=dumps({"schema": "proposal.v1", "project": "pimc", "agent": "idea",
        "research_question": "Human-authored input?", "hypothesis": "Lifecycle only.", "novelty": "No research claim."},
        "The test author approves this document; no model execution is represented."))
    session.graph.restore_state("idea", NodeState.WAITING_REVIEW)
    assert orch._spawn_owned(session, "lifecycle-synchronization", lambda: _wait(asyncio.Event()))
    if restart:
        await orch.shutdown_owned_runs()
        orch = Orchestrator(run_store=RunStore(tmp_path))
        session = orch.session(session.run.run_id)
    else:
        await orch.stop_owned_run(session.run.run_id)
    run_id = session.run.run_id
    assert not orch.start_owned_run(run_id)["ok"]
    assert not (await orch.resume_after_artifact_approval(run_id=run_id, agent="idea"))["ok"]
    approved = store.approve(artifact)  # Explicit author action, through the real artifact store.
    before = approved.path.read_bytes()
    result = await orch.resume_after_artifact_approval(run_id=run_id, agent="idea")
    assert result["ok"] and result["status"] == "completed"
    assert session.graph.state("idea") == NodeState.DONE and session.termination is None
    assert not orch.owned_tasks.stopping(run_id)
    rows = [json.loads(line) for line in (session.run.root / "events/run_lifecycle.jsonl").read_text().splitlines()]
    assert [row["event"] for row in rows] == ["run.cancelled", "run.approval_resumed"]
    assert rows[-1]["previous_termination"]["cleanup_complete"]
    assert rows[-1]["approved_sha256"] == hashlib.sha256(before).hexdigest()
    assert artifact.path.read_bytes() == approved.path.read_bytes() == before
    assert not (session.run.root / "agent_traces").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["interrupted", "running", "failed", "invalid_artifact", "cleanup_pending"])
async def test_approval_cannot_release_unsafe_stopped_execution(tmp_path: Path, blocker: str) -> None:
    orch, session = _session(tmp_path)
    session.graph.restore_state("idea", NodeState.WAITING_REVIEW)
    session.termination = {"type": "cancelled", "cleanup_complete": True, "interrupted_nodes": []}
    text = dumps({"schema": "proposal.v1", "project": "pimc", "agent": "idea",
        "research_question": "Human authored?", "hypothesis": "Test only.", "novelty": "No scientific claim."}, "Test input.")
    approved = session.run.subdir("idea") / "idea_proposal.approved.md"
    approved.write_text(text if blocker != "invalid_artifact" else "Invalid human-authored input")
    if blocker == "interrupted":
        session.termination["interrupted_nodes"] = ["idea"]
    elif blocker in {"running", "failed"}:
        session.graph.add_node("experiment", kind="agent")
        session.graph.restore_state("experiment", NodeState(blocker))
    elif blocker == "cleanup_pending":
        session.termination["cleanup_complete"] = False
    orch._persist_state(session, status="waiting_review")
    original = (session.run.root / "run_state.json").read_bytes()
    result = await orch.resume_after_artifact_approval(run_id=session.run.run_id, agent="idea")
    assert not result["ok"] and session.termination is not None
    assert (session.run.root / "run_state.json").read_bytes() == original
    assert not orch.owned_tasks.run_ids()


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True])
async def test_explicit_revision_after_safe_review_stop_owns_new_work_before_yield(tmp_path: Path, restart: bool) -> None:
    orch, session = _session(tmp_path)
    artifact = ArtifactStore(session.run).write(text=dumps({"schema": "proposal.v1", "project": "pimc", "agent": "idea",
        "research_question": "Human authored?", "hypothesis": "Test only.", "novelty": "No scientific claim."}, "Test input."))
    original = artifact.path.read_bytes()
    session.graph.restore_state("idea", NodeState.WAITING_REVIEW)
    assert orch._spawn_owned(session, "lifecycle-synchronization", lambda: _wait(asyncio.Event()))
    await orch.stop_owned_run(session.run.run_id)
    if restart:
        orch = Orchestrator(run_store=RunStore(tmp_path))
        session = orch.session(session.run.run_id)
    result = await orch.request_artifact_revision(run_id=session.run.run_id, agent="idea", reason="Explicit human edit request")
    assert result["status"] == "revision_started" and session.termination is None
    assert orch.owned_tasks.active(session.run.run_id) is not None
    # Stop before any Agent/provider execution. This proves task ownership,
    # not that a revised proposal could be generated or scientifically accepted.
    stopped = await orch.stop_owned_run(session.run.run_id)
    assert stopped["status"] == "stopped" and session.graph.state("idea") == NodeState.WAITING_REVIEW
    rows = [json.loads(line) for line in (session.run.root / "events/run_lifecycle.jsonl").read_text().splitlines()]
    resumed = next(row for row in rows if row["event"] == "run.revision_resumed")
    assert resumed["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert resumed["previous_termination"]["interrupted_nodes"] == []
    assert artifact.path.read_bytes() == original and not (session.run.root / "agent_traces").exists()


@pytest.mark.asyncio
async def test_revision_on_active_review_signals_once_and_does_not_replace_owner(tmp_path: Path) -> None:
    from app.hitl.review_session import ReviewSession

    reset_registry_for_tests()
    orch, session = _session(tmp_path)
    artifact = ArtifactStore(session.run).write(text=dumps({"schema": "proposal.v1", "project": "pimc", "agent": "idea",
        "research_question": "Human authored?", "hypothesis": "Test only.", "novelty": "No scientific claim."}, "Test input."))
    session.graph.restore_state("idea", NodeState.WAITING_REVIEW)
    review = ReviewSession(run=session.run, agent_name="idea", artifact_ref=artifact)
    await get_registry().register(review)
    assert orch._spawn_owned(session, "lifecycle-synchronization", lambda: _wait(asyncio.Event()))
    owner = orch.owned_tasks.active(session.run.run_id)
    async with session.bus.subscribe(f"run.{session.run.run_id}.hitl") as queue:
        for _ in range(2):
            result = await orch.request_artifact_revision(run_id=session.run.run_id, agent="idea", reason="Human revision")
            assert result["status"] == "revision_requested"
        assert queue.qsize() == 1
    assert review.regenerate_event.is_set() and orch.owned_tasks.active(session.run.run_id) is owner
    audit = [json.loads(line) for line in (session.run.subdir("hitl") / "review_log.jsonl").read_text().splitlines()]
    assert len(audit) == 1 and audit[0]["action"] == "regenerate"
    await orch.stop_owned_run(session.run.run_id)
    reset_registry_for_tests()


@pytest.mark.parametrize("pending,requests,responses", [("model", 1, 1), (None, 2, 1), (None, 1, 1)])
def test_cancelled_usage_is_unknown_with_unanswered_request(tmp_path: Path, pending: str | None, requests: int, responses: int) -> None:
    # Caller-authored counters test serialization only, not an execution/result.
    trace = tmp_path / "agent_traces/idea_research/counter_contract"
    trace.mkdir(parents=True)
    state: dict[str, Any] = {"status": "running", "history": [], "pending": pending, "usage_complete": True,
             "counts": {"model_requests": requests, "model_responses": responses, "tool_dispatches": 0}}
    path = trace / "checkpoint.json"
    path.write_text(json.dumps(state))
    before = path.read_bytes()
    record = archive_research_cancellation(root=tmp_path, trace=trace, target=tmp_path / "archive",
        delegation_id="counter_contract", min_sources=1, policy=AgentLoopPolicy(), gap="Counter contract", project="pimc")
    assert record["usage_complete"] is (pending != "model" and requests == responses)
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_unknown_historical_running_is_read_only_for_stop_start_and_retry(tmp_path: Path) -> None:
    orch, session = _session(tmp_path)
    session.graph.restore_state("idea", NodeState.RUNNING)
    orch._persist_state(session, status="running")
    path = session.run.root / "run_state.json"
    before = path.read_bytes()
    fresh = Orchestrator(run_store=RunStore(tmp_path))
    assert (await fresh.stop_owned_run(session.run.run_id))["status"] == "not_owned"
    assert fresh.start_owned_run(session.run.run_id)["status"] == "unowned_existing_execution"
    result = await fresh.request_artifact_revision(run_id=session.run.run_id, agent="idea", reason="No replay")
    assert result["status"] == "unowned_existing_execution"
    assert path.read_bytes() == before


def test_cancellation_archive_preserves_actual_checkpoint_bytes(tmp_path: Path) -> None:
    configured = os.environ.get("MARS_TEST_SOURCE_IDENTITY_CHECKPOINT")
    if not configured:
        pytest.skip("requires actual research checkpoint; no execution substitute")
    original = Path(configured)
    data = original.read_bytes()
    actual = json.loads(data)
    assert actual["history"] and actual["counts"]["model_requests"] > 0
    trace = tmp_path / "agent_traces/idea_research" / original.parent.name
    trace.mkdir(parents=True)
    (trace / "checkpoint.json").write_bytes(data)
    target = tmp_path / "idea/research_delegations" / original.parent.name
    # Pure cancellation-record serialization against immutable real input;
    # this does not claim that the original execution was cancelled.
    record = archive_research_cancellation(root=tmp_path, trace=trace, target=target,
        delegation_id=original.parent.name, min_sources=1, policy=AgentLoopPolicy(), gap="Archive contract", project="pimc")
    assert record["delegation_id"] == original.parent.name and record["failure_type"] == "research_cancelled"
    assert record["checkpoint_status"] == actual["status"] and record["pending"] == actual.get("pending")
    assert record["checkpoint_sha256"] == hashlib.sha256(data).hexdigest()
    assert not record["usable_as_final_evidence"] and not record["automatic_replay"]
    assert original.read_bytes() == (trace / "checkpoint.json").read_bytes() == data
    saved = (target / "failure.json").read_bytes()
    archive_research_cancellation(root=tmp_path, trace=trace, target=target, delegation_id=original.parent.name,
        min_sources=1, policy=AgentLoopPolicy(), gap="Archive contract", project="pimc")
    assert (target / "failure.json").read_bytes() == saved
