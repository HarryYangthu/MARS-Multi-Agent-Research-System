"""Phase A: durable run state, recovery on restart, and run commands."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.run_graph import RunGraph  # noqa: F401 (type clarity)
from app.harness.runtime.state_machine import NodeState, RunState
from app.storage import run_state_store
from app.storage.run_store import RunHandle, RunStore


def _orch(tmp_path: Path) -> Orchestrator:
    from app.bridge.agent_registry import AgentRegistry

    # Empty registry → stub runners, fully deterministic (no real LLM agents).
    return Orchestrator(
        run_store=RunStore(tmp_path),
        bus=InProcessEventBus(),
        registry=AgentRegistry(),
    )


@pytest.mark.asyncio
async def test_completed_run_persists_state(tmp_path: Path) -> None:
    orch = _orch(tmp_path)
    session = orch.create_session(
        RunRequest(task="persist", project="moe-pimc", auto_approve=True)
    )
    await orch.run(session.run.run_id)

    rec = run_state_store.read(session.run.root)
    assert rec is not None
    assert rec.run_status == RunState.COMPLETED.value
    assert all(n["state"] == "done" for n in rec.graph["nodes"])
    # transition log written
    log = (session.run.subdir("events") / "state_transitions.jsonl").read_text()
    assert '"scope": "run"' in log and '"scope": "node"' in log


@pytest.mark.asyncio
async def test_recovery_serves_finished_run_after_restart(tmp_path: Path) -> None:
    orch1 = _orch(tmp_path)
    session = orch1.create_session(
        RunRequest(task="restart", project="moe-pimc", auto_approve=True)
    )
    run_id = session.run.run_id
    await orch1.run(run_id)

    # Simulate a fresh process: brand-new orchestrator on the same runs dir.
    orch2 = _orch(tmp_path)
    assert run_id not in orch2._sessions
    loaded = orch2.get_or_load_session(run_id)
    assert loaded is not None
    assert loaded.run_status == RunState.COMPLETED
    assert all(s == NodeState.DONE for s in loaded.graph.all_states().values())


def test_recover_all_marks_interrupted_run(tmp_path: Path) -> None:
    # Hand-craft an in-flight run_state.json (status RUNNING, a node still running).
    store = RunStore(tmp_path)
    run: RunHandle = store.create(task="crashed", project="moe-pimc")
    from app.storage.run_state_store import RunStateRecord

    run_state_store.write(
        run.root,
        RunStateRecord(
            run_id=run.run_id,
            run_status=RunState.RUNNING.value,
            graph={
                "nodes": [
                    {"key": "idea", "kind": "agent", "state": "running", "metadata": {}},
                ],
                "edges": [],
                "entrypoints": ["idea"],
            },
        ),
    )

    orch = _orch(tmp_path)
    interrupted = orch.recover_all()
    assert run.run_id in interrupted
    assert orch.session(run.run_id).run_status == RunState.WAITING_HUMAN


def test_idempotency_key_dedupes(tmp_path: Path) -> None:
    orch = _orch(tmp_path)
    req = RunRequest(task="dedupe", project="moe-pimc")
    s1 = orch.create_session(req, idempotency_key="abc")
    s2 = orch.create_session(req, idempotency_key="abc")
    assert s1.run.run_id == s2.run.run_id


@pytest.mark.asyncio
async def test_cancel_before_run_yields_cancelled(tmp_path: Path) -> None:
    orch = _orch(tmp_path)
    session = orch.create_session(
        RunRequest(task="cancel", project="moe-pimc", auto_approve=True)
    )
    # Cooperative cancel: flag it, then drive — loop should stop immediately.
    session.cancel_requested = True
    await orch.run(session.run.run_id)
    assert session.run_status == RunState.CANCELLED
    rec = run_state_store.read(session.run.root)
    assert rec is not None and rec.run_status == RunState.CANCELLED.value


@pytest.mark.asyncio
async def test_retry_from_failed_node_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Isolate the *manual* retry path: disable the self-heal budget so a node
    # failure halts (rather than auto-re-routing, which Phase D adds).
    from app.execution.config import ExecutionConfig

    no_heal = ExecutionConfig(
        max_concurrency=6,
        default_steps=30,
        agent_batch_steps=20,
        backend="mock",
        job_timeout_seconds=120.0,
        feedback_max_attempts=0,
        planned_experiments=16,
        tick_seconds=0.0,
    )
    monkeypatch.setattr(
        "app.execution.config.get_execution_config", lambda: no_heal
    )

    orch = _orch(tmp_path)
    session = orch.create_session(
        RunRequest(task="retry", project="moe-pimc", auto_approve=True)
    )

    # Inject a runner that fails the coding node on first attempt only.
    calls = {"coding": 0}

    async def flaky_coding(run: RunHandle, key: str) -> None:
        calls["coding"] += 1
        if calls["coding"] == 1:
            raise RuntimeError("boom")

    session.runners["coding"] = flaky_coding

    await orch.run(session.run.run_id)
    assert session.graph.state("coding") == NodeState.FAILED
    # Compare on .value to avoid mypy narrowing the attribute across the await.
    assert session.run_status.value == "failed"

    # Retry: reset failed nodes and re-drive.
    orch.prepare_retry(session.run.run_id)
    assert session.graph.state("coding") == NodeState.PENDING
    await orch.run(session.run.run_id)
    assert session.run_status.value == "completed"
    assert all(s == NodeState.DONE for s in session.graph.all_states().values())
