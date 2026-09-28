"""Actual SQL barriers, CPU jobs and file leases; no model/service substitutes."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
import time

import pytest

from app.bridge.owned_run_tasks import OwnedRunTasks
from app.bridge.research_run_stop import (
    ResearchRunStops, ResearchStopPolicy, load_research_stop_policy,
    request_stop_barrier, reconcile_research_stop,
)
from app.bridge.research_stage_runtime import research_stage_dispatch
from app.harness.runtime.research_budget_ledger import BudgetAmounts, BudgetReservation
from app.harness.runtime.state_machine import NodeState
from tests.unit.test_research_job_service import _setup, _submit, _request, _wait, MEASURE
from tests.unit.test_research_job_control import wait_running
from tests.unit.test_research_stage_runtime import ready


def policy() -> ResearchStopPolicy:
    return ResearchStopPolicy(sqlite_timeout_seconds=0.05, poll_interval_seconds=0.02, reconciliation_seconds=2.0)


def test_configured_stop_bounds_are_finite() -> None:
    value = load_research_stop_policy()
    assert value.sqlite_timeout_seconds <= 1 and value.reconciliation_seconds <= 30


def test_barrier_rejects_new_actions_before_marking_run_cancelled(tmp_path: Path) -> None:
    stage = ready(tmp_path)
    payload = request_stop_barrier(stage.run, policy=policy(), reason="user_request")
    assert payload["status"] == "cancelling" and not payload["termination"]["cleanup_complete"]
    request = BudgetReservation(reservation_id="after-stop", operation_id="after-stop",
        operation_fingerprint="sha256:" + "1" * 64, kind="tool", amounts=BudgetAmounts(tool_executions=1))
    assert not stage.ledger.reserve(request).admitted
    with pytest.raises(ValueError, match="running SQL"):
        with research_stage_dispatch(stage):
            pytest.fail("stopped stage entered")
    response = reconcile_research_stop(stage.run, policy=policy(), owned_task_active=False)
    assert response["run_stop_confirmed"] and response["termination"]["cleanup_complete"]
    state = stage.ledger.journal.read()
    assert state["status"] == "cancelled" and state["graph"]["nodes"][0]["state"] == "failed"
    assert len(stage.ledger.journal.all_events()) == 2  # running, then failed
    before = stage.ledger.journal.path.read_bytes()
    assert request_stop_barrier(stage.run, policy=policy(), reason="user_request") == state
    assert reconcile_research_stop(stage.run, policy=policy(), owned_task_active=False)["run_stop_confirmed"]
    assert stage.ledger.journal.path.read_bytes() == before


def test_live_stage_lease_and_coroutine_each_prevent_false_stopped(tmp_path: Path) -> None:
    stage = ready(tmp_path)
    with research_stage_dispatch(stage):
        request_stop_barrier(stage.run, policy=policy(), reason="user_request")
        first = reconcile_research_stop(stage.run, policy=policy(), owned_task_active=False)
        assert not first["run_stop_confirmed"] and "stage_owner_unconfirmed" in first["unconfirmed"]
    second = reconcile_research_stop(stage.run, policy=policy(), owned_task_active=True)
    assert not second["run_stop_confirmed"] and "owned_coroutine_active" in second["unconfirmed"]
    assert reconcile_research_stop(stage.run, policy=policy(), owned_task_active=False)["run_stop_confirmed"]


def test_unknown_accounting_is_not_refunded_or_claimed_stopped(tmp_path: Path) -> None:
    stage = ready(tmp_path)
    request = BudgetReservation(reservation_id="unavailable", operation_id="unavailable",
        operation_fingerprint="sha256:" + "2" * 64, kind="tool", amounts=BudgetAmounts(tool_executions=1))
    assert stage.ledger.reserve(request).admitted
    stage.ledger.mark_unknown("unavailable", reason="Unfinished real accounting reservation, no tool success claimed")
    before = stage.ledger.snapshot().used
    request_stop_barrier(stage.run, policy=policy(), reason="user_request")
    response = reconcile_research_stop(stage.run, policy=policy(), owned_task_active=False)
    assert not response["run_stop_confirmed"] and "accounted_activity_unconfirmed" in response["unconfirmed"]
    assert stage.ledger.snapshot().used == before
    assert stage.ledger.snapshot().unknown_reservations == ("unavailable",)
    assert stage.ledger.journal.read()["status"] == "cancelling"


def test_two_real_jobs_stop_while_a_foreign_run_continues(tmp_path: Path) -> None:
    service, scope, _ = _setup(tmp_path / "owned", script="import time; time.sleep(3)\n" + MEASURE,
        budget_changes={"concurrent_training_jobs": 2, "training_job_seconds": 5})
    other, other_scope, _ = _setup(tmp_path / "foreign", script="import time; time.sleep(3)\n" + MEASURE,
        budget_changes={"training_job_seconds": 5})
    try:
        for index in (1, 2):
            _submit(service, scope, _request(f"job{index}", seed=index))
            wait_running(service, f"job{index}")
        _submit(other, other_scope)
        wait_running(other, "job1")
        request_stop_barrier(service.run, policy=policy(), reason="user_request")
        reconcile_research_stop(service.run, policy=policy(), owned_task_active=False)
        assert all(_wait(service, job).status == "cancelled" for job in ("job1", "job2"))
        final = reconcile_research_stop(service.run, policy=policy(), owned_task_active=False)
        assert final["run_stop_confirmed"] and service.ledger.journal.read()["status"] == "cancelled"
        assert not other.runner.status("job1").stop_requested
        assert _wait(other).status == "completed"
        assert int(service.ledger.snapshot().used["training_process_us"] or 0) > 0
    finally:
        for runner, job in ((service.runner, "job1"), (service.runner, "job2"), (other.runner, "job1")):
            runner.stop(job)


def test_missing_frozen_file_still_signals_owned_job_but_cannot_certify_budget(tmp_path: Path) -> None:
    service, scope, _ = _setup(tmp_path, script="import time; time.sleep(3)\n" + MEASURE,
                               budget_changes={"training_job_seconds": 5})
    try:
        _submit(service, scope)
        wait_running(service, "job1")
        (service.run.root / "input/research_task.v1.json").unlink()
        request_stop_barrier(service.run, policy=policy(), reason="user_request")
        first = reconcile_research_stop(service.run, policy=policy(), owned_task_active=False)
        assert not first["run_stop_confirmed"] and service.runner.status("job1").stop_requested
        assert _wait(service, raw=True).status == "cancelled"
        final = reconcile_research_stop(service.run, policy=policy(), owned_task_active=False)
        assert not final["run_stop_confirmed"] and "budget_evidence_unavailable" in final["unconfirmed"]
        assert service.ledger.journal.read()["status"] == "cancelling"
    finally:
        service.runner.stop("job1")


def test_actual_sql_lock_is_bounded_and_cannot_claim_persisted_barrier(tmp_path: Path) -> None:
    stage = ready(tmp_path)
    with sqlite3.connect(stage.ledger.journal.path) as connection:
        connection.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        with pytest.raises(ValueError):
            request_stop_barrier(stage.run, policy=policy(), reason="user_request")
        assert time.monotonic() - started < 0.5
    assert stage.ledger.journal.read()["status"] == "running"


@pytest.mark.asyncio
async def test_accepted_stop_runs_real_reconciliation_without_model_or_duplicate_submission(tmp_path: Path) -> None:
    service, scope, _ = _setup(tmp_path, script="import time; time.sleep(3)\n" + MEASURE,
                               budget_changes={"training_job_seconds": 5})
    controller = ResearchRunStops()
    owners = OwnedRunTasks()
    try:
        _submit(service, scope)
        wait_running(service, "job1")
        before = (service.root / "execution/local_jobs/job1/submission.json").read_bytes()
        started = time.monotonic()
        response = await controller.request(service.run, owners)
        assert time.monotonic() - started < 2
        assert response["status"] == "stop_requested" and not response["run_stop_confirmed"]
        task = controller.tasks[service.run.run_id]
        again = await controller.request(service.run, owners)
        assert again["ok"] and controller.tasks[service.run.run_id] is task
        await asyncio.wait_for(asyncio.shield(task), timeout=8)
        assert controller.results[service.run.run_id]["run_stop_confirmed"]
        assert (service.root / "execution/local_jobs/job1/submission.json").read_bytes() == before
        assert service.ledger.snapshot().used["model_requests"] == 0
    finally:
        service.runner.stop("job1")
        await asyncio.gather(*controller.tasks.values())


@pytest.mark.asyncio
async def test_new_orchestrator_stops_durable_job_without_in_memory_driver(tmp_path: Path) -> None:
    from app.bridge.orchestrator import Orchestrator
    from app.storage.run_store import RunStore
    service, scope, _ = _setup(tmp_path, script="import time; time.sleep(3)\n" + MEASURE,
                               budget_changes={"training_job_seconds": 5})
    owner = Orchestrator(run_store=RunStore(service.run.root.parent))
    try:
        _submit(service, scope)
        wait_running(service, "job1")
        assert owner.owned_tasks.active(service.run.run_id) is None
        response = await owner.stop_owned_run(service.run.run_id)
        assert response["status"] == "stop_requested"
        await owner.research_stops.wait(timeout=8)
        control = owner.run_control(service.run.run_id)
        assert control["cleanup_complete"] and not control["stopping"]
        assert control["research_stop"]["run_stop_confirmed"]
        assert service.runner.status("job1").status == "cancelled"
    finally:
        service.runner.stop("job1")
        await owner.research_stops.wait(timeout=8)


@pytest.mark.asyncio
async def test_cancelled_http_waiter_does_not_abandon_accepted_stop(tmp_path: Path) -> None:
    stage = ready(tmp_path)
    controller = ResearchRunStops()
    owners = OwnedRunTasks()
    with sqlite3.connect(stage.ledger.journal.path) as connection:
        connection.execute("BEGIN EXCLUSIVE")
        waiter = asyncio.create_task(controller.request(stage.run, owners))
        while stage.run.run_id not in controller.requests:
            await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not controller.requests[stage.run.run_id].cancelled()
    await controller.wait(timeout=5)
    assert controller.results[stage.run.run_id]["run_stop_confirmed"]
    assert stage.ledger.journal.read()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_sql_barrier_failure_still_cancels_actual_owned_coroutine(tmp_path: Path) -> None:
    stage = ready(tmp_path)
    controller = ResearchRunStops()
    owners = OwnedRunTasks()
    started, cleaned = asyncio.Event(), asyncio.Event()
    async def lifecycle_wait() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
    owners.spawn(stage.run.run_id, "lifecycle-wait", lifecycle_wait, finished=lambda: None)
    await started.wait()
    task = owners.active(stage.run.run_id)
    assert task is not None
    with sqlite3.connect(stage.ledger.journal.path) as connection:
        connection.execute("CREATE TRIGGER reject_stop BEFORE UPDATE ON run_state BEGIN SELECT RAISE(ABORT, 'actual SQL rejection'); END")
    result = await controller.request(stage.run, owners)
    assert result["status"] == "stop_state_error" and not result["state_persisted"]
    assert await owners.wait(task, timeout=1)
    assert cleaned.is_set() and stage.ledger.journal.read()["status"] == "running"
    await controller.wait(timeout=5)
    assert not controller.results[stage.run.run_id]["run_stop_confirmed"]


def test_actual_api_accepts_then_reports_verified_stop_after_owner_restart(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from app.api import dependencies
    from app.main import create_app
    from app.storage.run_store import RunStore
    service, scope, _ = _setup(tmp_path, script="import time; time.sleep(3)\n" + MEASURE,
                               budget_changes={"training_job_seconds": 5})
    dependencies.reset_for_tests()
    dependencies._run_store = RunStore(service.run.root.parent)
    try:
        _submit(service, scope)
        wait_running(service, "job1")
        with TestClient(create_app()) as client:
            response = client.post(f"/api/runs/{service.run.run_id}/stop")
            assert response.status_code == 202, response.text
            assert response.json()["status"] == "stop_requested"
            assert not response.json()["run_stop_confirmed"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                value = client.get(f"/api/runs/{service.run.run_id}/control").json()
                if value.get("research_stop", {}).get("run_stop_confirmed"):
                    break
                time.sleep(0.03)
            assert value["cleanup_complete"] and value["research_stop"]["run_stop_confirmed"]
            detail = client.get(f"/api/runs/{service.run.run_id}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["status"] == "cancelled"
            assert not detail.json()["execution_admission"]["ready"]
            assert service.runner.status("job1").status == "cancelled"
    finally:
        service.runner.stop("job1")
        dependencies.reset_for_tests()


@pytest.mark.asyncio
async def test_rejected_sql_barrier_still_signals_verified_actual_job(tmp_path: Path) -> None:
    service, scope, _ = _setup(tmp_path, script="import time; time.sleep(3)\n" + MEASURE,
                               budget_changes={"training_job_seconds": 5})
    controller = ResearchRunStops()
    try:
        _submit(service, scope)
        wait_running(service, "job1")
        with sqlite3.connect(service.ledger.journal.path) as connection:
            connection.execute("CREATE TRIGGER reject_stop BEFORE UPDATE ON run_state BEGIN SELECT RAISE(ABORT, 'actual SQL rejection'); END")
        result = await controller.request(service.run, OwnedRunTasks())
        assert result["status"] == "stop_state_error" and not result["state_persisted"]
        await controller.wait(timeout=5)
        assert service.runner.status("job1").stop_requested
        assert _wait(service).status == "cancelled"
        result = controller.results[service.run.run_id]
        assert result["jobs"] and not result["jobs"]["dispatch_barrier_observed"]
        assert not result["run_stop_confirmed"] and not result["state_persisted"]
        assert service.ledger.journal.read()["status"] != "cancelled"
    finally:
        service.runner.stop("job1")
        await controller.wait(timeout=5)


@pytest.mark.asyncio
async def test_failed_cache_notification_does_not_prevent_actual_stop(tmp_path: Path) -> None:
    stage = ready(tmp_path)
    controller, owners = ResearchRunStops(), OwnedRunTasks()
    started, cleaned = asyncio.Event(), asyncio.Event()
    async def lifecycle_wait() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
    owners.spawn(stage.run.run_id, "lifecycle-wait", lifecycle_wait, finished=lambda: None)
    await started.wait()
    def invalid_cache(state: dict[str, object]) -> None:
        raise ValueError("Actual notification consumer rejection")
    accepted = await controller.request(stage.run, owners, on_state=invalid_cache)
    assert accepted["state_persisted"]
    await controller.wait(timeout=5)
    assert cleaned.is_set() and controller.results[stage.run.run_id]["run_stop_confirmed"]
    assert stage.ledger.journal.read()["status"] == "cancelled"
