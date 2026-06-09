"""Phase D: self-heal feedback loop, diagnosis, failure_memory."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.bridge.agent_registry import AgentRegistry
from app.bridge.diagnosis import diagnose
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.kb.stores import reset_for_tests as reset_kb
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.runtime.state_machine import NodeState
from app.harness.schema.validator import validate_document
from app.storage.run_store import RunHandle, RunStore


def _orch(tmp_path: Path) -> Orchestrator:
    return Orchestrator(
        run_store=RunStore(tmp_path),
        bus=InProcessEventBus(),
        registry=AgentRegistry(),
    )


def test_failure_memory_recorded_and_retrievable(tmp_path: Path) -> None:
    stores = reset_kb(base=tmp_path)
    from app.harness.sedimentation.failure_memory import record_failure

    n = record_failure(
        project="moe-pimc",
        run_id="r1",
        node="execution",
        error="NaN loss after step 3",
    )
    assert n >= 1
    hits = stores.zone("failure_memory").query(query="execution NaN loss", top_k=1)
    assert hits, "failure should be retrievable from failure_memory zone"
    assert "execution" in hits[0][1].text


def test_diagnose_writes_valid_artifact(tmp_path: Path) -> None:
    run: RunHandle = RunStore(tmp_path).create(task="diag", project="moe-pimc")
    decision = diagnose(run, failed_node="execution", error="boom", attempt=1)
    assert decision.action == "revise_coding"
    assert decision.target_node == "coding"
    assert decision.artifact_path is not None
    text = Path(decision.artifact_path).read_text(encoding="utf-8")
    assert validate_document(text, expected_schema="diagnosis.v1").valid


@pytest.mark.asyncio
async def test_self_heal_reroutes_and_completes(tmp_path: Path) -> None:
    reset_kb(base=tmp_path)
    orch = _orch(tmp_path)
    session = orch.create_session(
        RunRequest(task="heal", project="moe-pimc", auto_approve=True)
    )

    calls = {"execution": 0}

    async def flaky_execution(run: RunHandle, key: str) -> None:
        calls["execution"] += 1
        if calls["execution"] == 1:
            raise RuntimeError("execution blew up on first try")

    session.runners["execution"] = flaky_execution

    await orch.run(session.run.run_id)

    assert session.run_status.value == "completed"
    assert session.feedback_attempts == 1
    assert calls["execution"] == 2  # failed once, succeeded after re-route
    assert all(s == NodeState.DONE for s in session.graph.all_states().values())
    # a diagnosis artifact was written
    diag_files = list((session.run.subdir("diagnosis")).glob("*.v1.md"))
    assert diag_files, "expected a diagnosis.v1 artifact"


@pytest.mark.asyncio
async def test_budget_exhaustion_halts(tmp_path: Path) -> None:
    reset_kb(base=tmp_path)
    orch = _orch(tmp_path)
    session = orch.create_session(
        RunRequest(task="nofix", project="moe-pimc", auto_approve=True)
    )

    async def always_fail(run: RunHandle, key: str) -> None:
        raise RuntimeError("permanently broken")

    session.runners["execution"] = always_fail

    await orch.run(session.run.run_id)

    # Re-routed up to the budget, then gave up.
    assert session.run_status.value == "failed"
    assert session.feedback_attempts == 2  # configs/execution.yaml max_attempts
    assert session.graph.state("execution") == NodeState.FAILED
