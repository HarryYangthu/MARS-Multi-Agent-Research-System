"""Stage-bound real ledger/lock recovery; checkpoints are never replayed here."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.bridge.run_model_recovery import recover_interrupted_run_model_requests
from app.bridge.task_runtime import bind_task, resumable_task
from app.harness.agent_loop.trace import LoopTrace, audit_trace
from app.harness.llm.accounting import RunModelBudget
from app.harness.llm.provider_base import Message
from app.storage.run_store import RunHandle
from tests.unit.test_commander_model_recovery import _config, _orphan, _policy, _state


def _bound_stage(root: Path, stage: str = "coding", *, pending: str = "model",
                 pending_batch: bool = False, project: str = "test7") -> tuple[RunHandle, dict[str, Any], Path]:
    _policy(root)
    run = RunHandle(run_id=root.name, root=root, project=project, task="recovery",
                    entrypoint=stage, created_at="2026-10-21T00:00:00Z")
    task = bind_task(run, stage, goal="Recover a real model reservation", upstream={}, output_schema="code_spec.v1")
    correlation = {"trace_id": run.run_id, "node_id": stage, "task_id": task.task_id,
                   "parent_task_id": task.parent_task_id, "invocation_id": task.invocation_id}
    trace = LoopTrace(root / "agent_traces" / stage / task.invocation_id, "full", correlation=correlation)
    # This is caller-authored checkpoint evidence for admission tests. It does
    # not contain a provider response, tool success or a measured experiment.
    trace.emit("model_request", {"request": 1, "phase": "act", "provider": "openai", "model": "test-model"})
    state = {"fingerprint": "host-checkpoint-admission-fixture", "status": "interrupted", "pending": pending,
             "pending_batch": [{"tool": "unknown-operation"}] if pending_batch else [],
             "counts": {"model_requests": 1, "model_responses": 0, "tool_dispatches": 0,
                        "observations": 0, "sdk_attempts": 0, "reflections": 0},
             "usage": {}, "usage_complete": False, "correlation": correlation, "next_phase": "act"}
    trace.snapshot(state)
    return run, correlation, trace.root / "checkpoint.json"


@pytest.mark.parametrize("stage", ["idea", "experiment", "coding", "writing"])
def test_bound_stage_model_owner_loss_is_acknowledged_without_touching_checkpoint(tmp_path: Path, stage: str) -> None:
    run, correlation, checkpoint = _bound_stage(tmp_path, stage)
    request_id = _orphan(tmp_path, correlation)
    before = _state(tmp_path)["requests"][request_id]
    checkpoint_before = checkpoint.read_bytes()
    result = recover_interrupted_run_model_requests(run)
    assert result["status"] == "recovered" and result["recovered_request_ids"] == [request_id]
    after = _state(tmp_path)["requests"][request_id]
    assert after["status"] == "abandoned" and after["usage"] is None and not after["usage_complete"]
    for field in ("charged_tokens", "charged_input_tokens", "charged_output_tokens", "charged_cost", "charged_attempts"):
        assert after[field] == before[field]
    assert checkpoint.read_bytes() == checkpoint_before
    assert recover_interrupted_run_model_requests(run)["status"] == "idle"
    # Existing history/counts stay in the checkpoint; a verified receipt permits
    # the next generation instead of restarting the whole stage or its tools.
    restored = resumable_task(run, stage)
    assert restored.node_id == stage


@pytest.mark.parametrize("pending,batch", [("tool", False), ("model", True)])
def test_unknown_tool_or_batch_is_never_replayed_or_reconciled_as_a_model(tmp_path: Path, pending: str, batch: bool) -> None:
    run, correlation, checkpoint = _bound_stage(tmp_path, pending=pending, pending_batch=batch)
    _orphan(tmp_path, correlation)
    before = (tmp_path / "resources/model_budget.v1.json").read_bytes()
    saved = checkpoint.read_bytes()
    result = recover_interrupted_run_model_requests(run)
    assert result["status"] == "blocked" and "工具" in result["message"]
    assert (tmp_path / "resources/model_budget.v1.json").read_bytes() == before
    assert checkpoint.read_bytes() == saved


def test_live_stage_owner_lease_is_not_treated_as_dead(tmp_path: Path) -> None:
    run, correlation, _checkpoint = _bound_stage(tmp_path)
    owner = RunModelBudget(tmp_path)
    reservation = owner.reserve([Message("user", "Actual active reservation")], _config(), correlation)
    before = owner.path.read_bytes()
    try:
        result = recover_interrupted_run_model_requests(run)
        assert result["status"] == "blocked" and "仍在运行" in result["message"]
        assert owner.path.read_bytes() == before
    finally:
        owner.settle(reservation, usage=None, complete=False, outcome="cancelled")


@pytest.mark.parametrize("changes", [{"trace_id": "other-run"}, {"task_id": "other-task"},
                                     {"parent_task_id": "other-parent"}, {"invocation_id": "foreign-invocation"}])
def test_wrong_task_binding_cannot_acknowledge_an_orphan(tmp_path: Path, changes: dict[str, Any]) -> None:
    run, correlation, _checkpoint = _bound_stage(tmp_path)
    _orphan(tmp_path, {**correlation, **changes})
    before = (tmp_path / "resources/model_budget.v1.json").read_bytes()
    assert recover_interrupted_run_model_requests(run)["status"] == "blocked"
    assert (tmp_path / "resources/model_budget.v1.json").read_bytes() == before


def test_missing_or_stale_trace_evidence_cannot_be_accepted_as_recovery(tmp_path: Path) -> None:
    run, correlation, checkpoint = _bound_stage(tmp_path)
    _orphan(tmp_path, correlation)
    value = json.loads(checkpoint.read_text())
    value["correlation"] = {**correlation, "node_id": "different-node"}
    checkpoint.write_text(json.dumps(value), encoding="utf-8")
    before = (tmp_path / "resources/model_budget.v1.json").read_bytes()
    assert recover_interrupted_run_model_requests(run)["status"] == "blocked"
    assert (tmp_path / "resources/model_budget.v1.json").read_bytes() == before


def test_only_sdk_metadata_snapshot_gap_can_be_repaired_after_real_owner_death(tmp_path: Path) -> None:
    run, correlation, checkpoint = _bound_stage(tmp_path)
    request_id = _orphan(tmp_path, correlation)
    original = checkpoint.read_bytes()
    # Caller-authored pure journal fixture for the append-before-snapshot gap;
    # it describes retry metadata, never a model response or tool result.
    trace = LoopTrace(checkpoint.parent, "full", resume=True, correlation=correlation)
    trace.emit("sdk_retry_scheduled", {"next_attempt": 2, "delay": 20})
    assert not audit_trace(checkpoint.parent)["consistent"]
    result = recover_interrupted_run_model_requests(run)
    assert result["status"] == "recovered" and result["metadata_snapshots_recovered"] == [request_id]
    assert audit_trace(checkpoint.parent)["consistent"]
    assert checkpoint.read_bytes() == original
    assert resumable_task(run, "coding")


def test_metadata_gap_cannot_be_repaired_while_owner_is_alive(tmp_path: Path) -> None:
    run, correlation, checkpoint = _bound_stage(tmp_path)
    owner = RunModelBudget(tmp_path)
    reservation = owner.reserve([Message("user", "Actual active owner")], _config(), correlation)
    trace = LoopTrace(checkpoint.parent, "full", resume=True, correlation=correlation)
    trace.emit("sdk_retry_scheduled", {"next_attempt": 2, "delay": 20})
    paths = [checkpoint, checkpoint.with_name("facts.json"), owner.path]
    before = {path: path.read_bytes() for path in paths}
    try:
        assert recover_interrupted_run_model_requests(run)["status"] == "blocked"
        assert {path: path.read_bytes() for path in paths} == before
    finally:
        owner.settle(reservation, usage=None, complete=False, outcome="cancelled")


@pytest.mark.parametrize("kind", ["tool_dispatch", "model_response"])
def test_snapshot_gap_cannot_hide_a_received_response_or_possible_tool_effect(tmp_path: Path, kind: str) -> None:
    run, correlation, checkpoint = _bound_stage(tmp_path)
    _orphan(tmp_path, correlation)
    trace = LoopTrace(checkpoint.parent, "full", resume=True, correlation=correlation)
    trace.emit(kind, {"admission_rejection_fixture": True})
    paths = [checkpoint, checkpoint.with_name("facts.json"), tmp_path / "resources/model_budget.v1.json"]
    before = {path: path.read_bytes() for path in paths}
    assert recover_interrupted_run_model_requests(run)["status"] == "blocked"
    assert {path: path.read_bytes() for path in paths} == before


def test_first_owner_recovery_does_not_invalidate_its_own_resume_token(tmp_path: Path) -> None:
    from app.bridge.run_recovery import recovery_status
    from tests.unit.test_run_recovery import session_at

    orch, session = session_at(tmp_path)
    _run, correlation, _checkpoint = _bound_stage(session.run.root, "idea", project=session.run.project)
    _orphan(session.run.root, correlation)
    initial = recovery_status(orch, session.run.run_id, project=session.run.project)
    refreshed = recovery_status(orch, session.run.run_id, project=session.run.project)
    assert initial["status"] == refreshed["status"] == "recoverable"
    assert {row["action"] for row in initial["actions"]} == {"resume", "retry"}
    assert initial["token"] == refreshed["token"]
