"""Real owned process control; damaged jobs never synthesize success."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from app.bridge.research_job_control import JobControlPolicy, control_owned_jobs, load_job_control_policy
from tests.unit.test_research_job_service import _request, _setup, _submit, _wait, MEASURE

pytestmark = pytest.mark.skipif(os.name != "posix", reason="trusted local CPU adapter requires POSIX")


def wait_running(service: object, job: str) -> None:
    from app.bridge.research_job_service import ResearchJobService
    assert isinstance(service, ResearchJobService)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if service.runner.status(job).status == "running":
            return
        time.sleep(0.02)
    raise AssertionError("Actual worker did not start")


def test_default_control_policy_has_finite_positive_lock_wait() -> None:
    value = load_job_control_policy()
    assert 0 < value.lock_timeout_seconds <= 5


def test_empty_pass_never_claims_run_stopped_or_changes_state(tmp_path: Path) -> None:
    service, _, _ = _setup(tmp_path)
    before = service.ledger.journal.path.read_bytes()
    result = control_owned_jobs(service, action="stop", policy=load_job_control_policy())
    assert not result.jobs and not result.failures and not result.observed_job_ids
    assert result.all_observed_jobs_terminal and not result.dispatch_barrier_observed
    assert not result.run_stop_confirmed
    assert service.ledger.journal.path.read_bytes() == before


def test_two_running_jobs_stop_without_restarting_and_settle_only_on_real_receipts(tmp_path: Path) -> None:
    service, scope, _ = _setup(tmp_path, script="import time; time.sleep(2)\n" + MEASURE,
                               budget_changes={"concurrent_training_jobs": 2, "training_job_seconds": 4})
    try:
        for index in (1, 2):
            _submit(service, scope, _request(f"job{index}", seed=index))
            wait_running(service, f"job{index}")
        before = service.ledger.journal.read()
        value = control_owned_jobs(service, action="stop", policy=load_job_control_policy())
        assert not value.failures and len(value.jobs) == 2
        assert all(item.stop_requested for item in value.jobs)
        assert not value.run_stop_confirmed and not value.dispatch_barrier_observed
        assert service.ledger.journal.read() == before
        assert all(_wait(service, job).status == "cancelled" for job in ("job1", "job2"))
        value = control_owned_jobs(service, action="reconcile", policy=load_job_control_policy())
        assert value.all_observed_jobs_terminal and not value.run_stop_confirmed
        assert service.ledger.snapshot().active_jobs == 0
        assert len(list((service.root / "execution/local_jobs").iterdir())) == 2
    finally:
        for job in ("job1", "job2"):
            try:
                service.runner.stop(job)
            except ValueError:
                pass


def test_bad_first_job_does_not_prevent_second_owned_job_stop(tmp_path: Path) -> None:
    service, scope, _ = _setup(tmp_path, script="import time; time.sleep(2)\n" + MEASURE,
                               budget_changes={"concurrent_training_jobs": 2, "training_job_seconds": 4})
    try:
        for index in (1, 2):
            _submit(service, scope, _request(f"job{index}", seed=index))
            wait_running(service, f"job{index}")
        with service.ledger.journal.transaction() as connection:
            connection.execute("UPDATE research_jobs SET binding_sha256='corrupt' WHERE job_id='job1'")
        result = control_owned_jobs(service, action="stop", policy=load_job_control_policy())
        assert [failure.job_id for failure in result.failures] == ["job1"]
        assert [view.job_id for view in result.jobs] == ["job2"]
        assert result.jobs[0].stop_requested and not result.all_observed_jobs_terminal
        assert not (service.root / "execution/local_jobs/job1/stop.json").exists()
        assert _wait(service, "job2").status == "cancelled"
    finally:
        service.runner.stop("job1")
        service.runner.stop("job2")


@pytest.mark.parametrize("which", ["outer", "inner"])
def test_actual_held_lock_is_bounded_and_later_job_is_controlled(tmp_path: Path, which: str) -> None:
    service, scope, _ = _setup(tmp_path, script="import time; time.sleep(2)\n" + MEASURE,
                               budget_changes={"concurrent_training_jobs": 2, "training_job_seconds": 4})
    child: subprocess.Popen[bytes] | None = None
    try:
        for index in (1, 2):
            _submit(service, scope, _request(f"job{index}", seed=index))
            wait_running(service, f"job{index}")
        lock = service.root / ("execution/research_job_controls/job1.lock" if which == "outer"
                               else "execution/local_jobs/job1/control.lock")
        ready = tmp_path / "ready"
        script = "from filelock import FileLock\nfrom pathlib import Path\nimport sys,time\nwith FileLock(sys.argv[1]):\n Path(sys.argv[2]).write_text('owned')\n time.sleep(8)\n"
        child = subprocess.Popen([sys.executable, "-c", script, str(lock), str(ready)])
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        started = time.monotonic()
        result = control_owned_jobs(service, action="stop", policy=JobControlPolicy(lock_timeout_seconds=0.05))
        assert time.monotonic() - started < 2
        assert [failure.job_id for failure in result.failures] == ["job1"]
        assert result.jobs[0].job_id == "job2" and result.jobs[0].stop_requested
        assert not result.all_observed_jobs_terminal
    finally:
        if child is not None:
            child.terminate()
            child.wait(timeout=5)
        service.runner.stop("job1")
        service.runner.stop("job2")


@pytest.mark.parametrize("content", ["[]", "null", "42", '"invalid"', "{broken"])
def test_damaged_first_submission_cannot_abort_control_of_later_jobs(tmp_path: Path, content: str) -> None:
    service, scope, _ = _setup(tmp_path, script="import time; time.sleep(2)\n" + MEASURE,
                               budget_changes={"concurrent_training_jobs": 2, "training_job_seconds": 4})
    path = service.root / "execution/local_jobs/job1/submission.json"
    original: bytes | None = None
    try:
        for index in (1, 2):
            _submit(service, scope, _request(f"job{index}", seed=index))
            wait_running(service, f"job{index}")
        original = path.read_bytes()
        path.write_text(content)
        result = control_owned_jobs(service, action="stop", policy=load_job_control_policy())
        assert [failure.job_id for failure in result.failures] == ["job1"]
        assert len(result.jobs) == 1 and result.jobs[0].job_id == "job2"
        assert result.jobs[0].stop_requested and not result.all_observed_jobs_terminal
        assert not result.run_stop_confirmed
        assert _wait(service, "job2").status == "cancelled"
    finally:
        if original is not None:
            path.write_bytes(original)
        for job in ("job1", "job2"):
            service.runner.stop(job)
            _wait(service, job, raw=True)


def test_missing_frozen_input_still_stops_owned_work_without_refunding(tmp_path: Path) -> None:
    service, scope, _ = _setup(tmp_path, script="import time; time.sleep(2)\n" + MEASURE,
                               budget_changes={"training_job_seconds": 4})
    _submit(service, scope)
    wait_running(service, "job1")
    (service.root / "input/research_task.v1.json").unlink()
    result = control_owned_jobs(service, action="stop", policy=load_job_control_policy())
    assert not result.failures and result.jobs[0].stop_requested
    assert result.jobs[0].budget_state == "unknown"
    assert _wait(service, raw=True).status == "cancelled"
    with service.ledger.journal.connection() as connection:
        assert connection.execute("SELECT state FROM research_reservations").fetchall() == [("reserved",)]


def test_invalid_catalog_row_does_not_hide_other_valid_owned_jobs(tmp_path: Path) -> None:
    service, scope, _ = _setup(tmp_path, script="import time; time.sleep(2)\n" + MEASURE,
                               budget_changes={"concurrent_training_jobs": 2, "training_job_seconds": 4})
    try:
        for index in (1, 2):
            _submit(service, scope, _request(f"job{index}", seed=index))
            wait_running(service, f"job{index}")
        with service.ledger.journal.transaction() as connection:
            connection.execute("UPDATE research_jobs SET job_id='../damaged-row' WHERE job_id='job1'")
        result = control_owned_jobs(service, action="stop", policy=load_job_control_policy())
        assert result.observed_job_ids == ("job2",) and result.jobs[0].stop_requested
        assert result.unverified_catalog_rows == 1
        assert not result.catalog_unchanged and not result.all_observed_jobs_terminal and not result.run_stop_confirmed
        assert "damaged-row" not in result.model_dump_json()
        assert not (service.root / "execution/damaged-row").exists()
        assert _wait(service, "job2").status == "cancelled"
    finally:
        for job in ("job1", "job2"):
            service.runner.stop(job)
            _wait(service, job, raw=True)


def test_reconcile_does_not_submit_or_stop_an_unowned_runner_job(tmp_path: Path) -> None:
    from app.execution.local.runner import LocalJobSpec
    service, _, code = _setup(tmp_path, script="import time; time.sleep(2)\n" + MEASURE)
    spec = LocalJobSpec(run_id=service.run.run_id, attempt_id="unowned", job_id="unowned", experiment_id="unowned",
        project=service.run.project, argv=(sys.executable, str(code / "command.py")), cwd=str(code),
        timeout_seconds=4.0, steps=1, required_metrics=("mse",), max_output_bytes=10000)
    service.runner.submit(spec)
    try:
        wait_running(service, "unowned")
        result = control_owned_jobs(service, action="stop", policy=load_job_control_policy())
        assert result.observed_job_ids == ()
        assert not (service.root / "execution/local_jobs/unowned/stop.json").exists()
        assert service.runner.status("unowned").owner_active
    finally:
        service.runner.stop("unowned")


def test_cancel_barrier_is_observation_only(tmp_path: Path) -> None:
    service, scope, _ = _setup(tmp_path)
    _submit(service, scope)
    _wait(service)
    with service.ledger.journal.transaction() as connection:
        payload = service.ledger.journal._read(connection)
        payload["status"] = "cancelled"
        connection.execute("UPDATE run_state SET payload=? WHERE id=1", (json.dumps(payload),))
    before = service.ledger.journal.path.read_bytes()
    result = control_owned_jobs(service, action="reconcile", policy=load_job_control_policy())
    assert result.dispatch_barrier_observed and result.all_observed_jobs_terminal
    assert not result.run_stop_confirmed and service.ledger.journal.path.read_bytes() == before
