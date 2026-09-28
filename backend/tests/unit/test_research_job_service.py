"""Real CPU execution and SQLite recovery, without service or process doubles."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from typing import Any

from filelock import FileLock
import pytest

from app.bridge.research_contract_service import default_research_budget, freeze_research_task
from app.bridge.orchestrator import Orchestrator
from app.bridge.research_job_service import ResearchJobRejected, ResearchJobRequest, ResearchJobService, ResearchJobView, load_research_job_policy
from app.bridge.research_project_scope import prepare_project_scope
from app.bridge.research_run_service import create_research_run
from app.execution.local.runner import LocalJobSpec, LocalRunner
from app.harness.discovery.snapshots import SnapshotPolicy
from app.harness.runtime.project_scope import ProjectScope, bind_project_scope
from app.harness.runtime.research_budget_ledger import BudgetAmounts, BudgetReservation, ResearchBudgetLedger
from app.harness.runtime.research_contract import ProjectContract, ResearchBudget
from app.harness.runtime.research_execution_scope import ResearchExecutionScope, bind_research_execution
from app.harness.runtime.state_journal import StateJournal
from app.storage.run_store import RunHandle, RunStore

pytestmark = pytest.mark.skipif(os.name != "posix", reason="trusted local CPU adapter requires POSIX")

MEASURE = '''import json, os
from pathlib import Path
request = json.loads(Path(os.environ['MARS_JOB_REQUEST']).read_text())
root = Path(request['output_dir'])
x = [1.0, 2.0, 3.0]
errors = [(2.0 * value - 1.5 * value) ** 2 for value in x]
(root / 'measurements.json').write_text(json.dumps({'x': x, 'squared_errors': errors}))
Path(os.environ['MARS_RESULT_PATH']).write_text(json.dumps({'schema': 'local_command_result.v1', 'invocation_id': request['invocation_id'], 'run_id': request['run_id'], 'experiment_id': request['experiment_id'], 'status': 'completed', 'metrics': {'mse': sum(errors) / len(errors)}, 'evidence_paths': ['measurements.json']}))
'''


def _setup(root: Path, *, script: str = MEASURE, budget_changes: dict[str, Any] | None = None,
           data: bool = False, device: str = "cpu", args: list[str] | None = None) -> tuple[ResearchJobService, ProjectScope, Path]:
    root = root.resolve()
    code = root / "source"
    code.mkdir(parents=True)
    (code / "baseline.py").write_text("BASELINE = 1\n")
    (code / "command.py").write_text(script)
    external = root / "data"
    external.mkdir()
    project = ProjectContract.model_validate({"project_id": "cpu_contract", "display_name": "Measured CPU job",
        "paths": {"code": str(code), "knowledge": [], "data": [str(external)] if data else [], "output": str(root / "output")},
        "commands": [{"name": purpose, "purpose": purpose, "executable": sys.executable,
            "arguments": args or ["command.py"], "entrypoint_files": ["command.py"]} for purpose in ("check", "train", "evaluate")],
        "metrics": [{"name": "mse", "unit": "unitless", "direction": "minimize", "target": 0.0, "tolerance": 0.0}],
        "baseline_files": ["baseline.py"], "allowed_paths": ["command.py", "candidate.py"], "protected_paths": [],
        "execution": {"kind": "local", "device": device}})
    budget = ResearchBudget.model_validate({**default_research_budget().model_dump(), "training_job_seconds": 3,
        **(budget_changes or {})})
    frozen = freeze_research_task(project, goal="Measure actual squared error", mode="manual", budget=budget)
    owner = Orchestrator(run_store=RunStore(root / "runs"))
    run = create_research_run(owner, name="real-cpu-job", contract=frozen).run
    journal = StateJournal.from_authority(run.root, run_id=run.run_id)
    assert journal is not None
    ledger = ResearchBudgetLedger(journal, task_sha256=frozen.task_sha256, budget=budget)
    ledger.initialize()
    scope = prepare_project_scope(run, candidate_id="candidate", snapshot_policy=SnapshotPolicy(allowed_paths=("*",)))
    service = ResearchJobService(run, ledger, load_research_job_policy())
    service.initialize()
    return service, scope, code


def _request(job: str = "job1", **changes: Any) -> ResearchJobRequest:
    return ResearchJobRequest.model_validate({"job_id": job, "attempt_id": "attempt1", "experiment_id": job,
        "command_name": "train", "steps": 1, **changes})


def _submit(service: ResearchJobService, scope: ProjectScope, request: ResearchJobRequest | None = None) -> ResearchJobView:
    with bind_project_scope(scope), bind_research_execution(ResearchExecutionScope(service.ledger, "execution", "host-invocation")):
        return service.submit(request or _request(), scope)


def _wait(service: ResearchJobService, job: str = "job1", *, raw: bool = False) -> Any:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        value = service.runner.status(job) if raw else service.status(job)
        if value.receipt is not None:
            return value
        time.sleep(0.03)
    service.runner.stop(job)
    raise AssertionError("Actual CPU command did not publish a receipt")


def test_real_job_binds_command_snapshot_metrics_and_settles_shared_sqlite(tmp_path: Path) -> None:
    service, scope, code = _setup(tmp_path)
    initial = _submit(service, scope)
    assert initial.status in {"queued", "running"} and initial.budget_state == "reserved"
    assert service.ledger.snapshot().active_jobs == 1
    result = _wait(service)
    assert result.status == "completed" and result.budget_state == "settled"
    assert result.receipt["metrics"]["mse"] == pytest.approx(3.5 / 3)
    assert result.receipt["os_isolated"] is False and result.receipt["device"] == "cpu"
    assert not (code / "measurements.json").exists()
    snapshot = service.ledger.snapshot()
    assert snapshot.active_jobs == 0 and snapshot.active_gpus == 0
    assert 0 < int(snapshot.used["training_process_us"] or 0) < 3_000_000 and not snapshot.reservation_overrun
    receipt_path = service.root / "execution/local_jobs/job1/execution_receipt.json"
    original = receipt_path.read_bytes()
    authority = service.ledger.journal.path.read_bytes()
    assert _submit(service, scope).receipt == result.receipt
    assert receipt_path.read_bytes() == original
    assert service.ledger.journal.path.read_bytes() == authority
    from app.bridge.results_service import ResultReader, load_results_policy
    reader = ResultReader(service.run, load_results_policy())
    experiments, metrics, _ = reader.jobs()
    assert not reader.limitations and experiments[0]["verification"] == "verified_local_receipt"
    assert metrics[0]["value"] == pytest.approx(3.5 / 3)
    with service.ledger.journal.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM research_jobs").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM research_reservations").fetchone() == (1,)


def test_late_recovery_uses_finished_time_without_live_source_or_clock_rollback(tmp_path: Path) -> None:
    service, scope, code = _setup(tmp_path)
    _submit(service, scope)
    actual = _wait(service, raw=True)
    finished_us = math.ceil(actual.receipt["finished_at"] * 1_000_000)
    time.sleep(0.08)
    with service.ledger.transaction() as transaction:
        transaction.observe_clock()
    shutil.rmtree(code)
    restored = ResearchJobService(service.run, service.ledger, service.policy)
    result = restored.status("job1")
    assert result.status == "completed" and result.budget_state == "settled"
    with service.ledger.journal.connection() as connection:
        start, end = connection.execute("SELECT started_us,ended_us FROM research_activity").fetchone()
    assert end == finished_us
    assert service.ledger.snapshot().activity_us == end - start
    assert not service.ledger.snapshot().clock_uncertain
    assert restored.reconcile_all()[0].receipt == actual.receipt


def test_unknown_then_valid_terminal_receipt_closes_activity_without_refund(tmp_path: Path) -> None:
    service, scope, _ = _setup(tmp_path)
    _submit(service, scope)
    actual = _wait(service, raw=True)
    submission = json.loads((service.root / "execution/local_jobs/job1/submission.json").read_text())
    execution_snapshot = Path(submission["spec"]["cwd"])
    hidden = execution_snapshot.with_name(execution_snapshot.name + "_temporarily_missing")
    execution_snapshot.rename(hidden)
    try:
        assert service.status("job1").budget_state == "unknown"
        assert service.ledger.snapshot().active_jobs == 1
    finally:
        hidden.rename(execution_snapshot)
    result = service.status("job1")
    assert result.status == "completed" and result.budget_state == "retained"
    snapshot = service.ledger.snapshot()
    assert snapshot.used["training_process_us"] == 3_000_000
    assert snapshot.active_jobs == 0 and snapshot.unknown_reservations == ("job:job1",)
    with service.ledger.journal.connection() as connection:
        end = connection.execute("SELECT ended_us FROM research_activity").fetchone()[0]
    assert end == math.ceil(actual.receipt["finished_at"] * 1_000_000)
    assert service.status("job1").budget_state == "retained"


@pytest.mark.parametrize("reason", ["slot", "process", "lifecycle"])
def test_budget_rejection_cannot_spawn_a_runner(tmp_path: Path, reason: str) -> None:
    service, scope, _ = _setup(tmp_path)
    if reason == "lifecycle":
        payload = service.ledger.journal.read()
        payload["status"] = "cancelled"
        service.ledger.journal.commit(payload, expected_revision=payload["revision"])
    else:
        amount = 1 if reason == "slot" else service.ledger.budget.training_process_seconds * 1_000_000
        spec = BudgetReservation(reservation_id="occupied", operation_id="occupied", operation_fingerprint="sha256:" + "b" * 64,
            kind="job", amounts=BudgetAmounts(training_process_us=amount), job_duration_us=1)
        service.ledger.reserve(spec)
        if reason == "process":
            service.ledger.mark_unknown("occupied", reason="actual accounting reservation remains unavailable")
            service.ledger.reconcile_stopped("occupied", actor="test-accounting", evidence_refs=("pure-accounting-input",))
    with pytest.raises(ResearchJobRejected, match="budget refused"):
        _submit(service, scope)
    assert not (service.root / "execution/local_jobs").exists()
    with service.ledger.journal.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM research_jobs").fetchone() == (0,)


@pytest.mark.parametrize("constraint", ["data", "argv", "missing_command", "unbound"])
def test_unsupported_contract_execution_is_rejected_before_dispatch(tmp_path: Path, constraint: str) -> None:
    service, scope, _ = _setup(tmp_path, data=constraint == "data", device="gpu" if constraint == "gpu" else "cpu",
        args=["command.py", "--extra"] if constraint == "argv" else None)
    with pytest.raises(ResearchJobRejected):
        if constraint == "unbound":
            service.submit(_request(), scope)
        else:
            _submit(service, scope, _request(command_name="missing" if constraint == "missing_command" else "train"))
    assert not (service.root / "execution/local_jobs").exists()
    assert service.ledger.snapshot().active_jobs == 0


def test_gpu_contract_is_already_blocked_by_real_freeze_preflight(tmp_path: Path) -> None:
    from app.bridge.research_contract_service import ProjectPreflightError
    with pytest.raises(ProjectPreflightError, match="GPU capability"):
        _setup(tmp_path, device="gpu")
    assert not (tmp_path / "runs").exists()


def test_cpu_job_import_does_not_create_snapshot_bytecode(tmp_path: Path) -> None:
    service, scope, _ = _setup(tmp_path, script="import candidate\nassert candidate.VALUE == 7\n" + MEASURE)
    (scope.candidate_root / "candidate.py").write_text("VALUE = 7\n")
    _submit(service, scope)
    assert _wait(service).status == "completed"
    assert not list((service.root / "execution/research_job_snapshots").rglob("__pycache__"))


def test_actual_deadline_and_owned_stop_have_terminal_receipts(tmp_path: Path) -> None:
    service, scope, _ = _setup(tmp_path / "deadline", script="import time\ntime.sleep(30)\n", budget_changes={"training_job_seconds": 1})
    _submit(service, scope)
    result = _wait(service)
    assert result.status == "failed" and result.receipt["error"] == "deadline_exceeded"
    assert service.ledger.snapshot().active_jobs == 0
    other, other_scope, _ = _setup(tmp_path / "stop", script="import time\ntime.sleep(30)\n")
    _submit(other, other_scope)
    with pytest.raises(ValueError, match="not owned"):
        other.stop("foreign_job")
    intent = other.stop("job1")
    assert intent.stop_requested
    cancelled = _wait(other)
    assert cancelled.status == "cancelled" and cancelled.budget_state == "settled"


def test_missing_frozen_input_does_not_block_owned_stop_or_later_refund_unknown(tmp_path: Path) -> None:
    service, scope, _ = _setup(tmp_path, script="import time\ntime.sleep(30)\n")
    _submit(service, scope)
    contract = service.root / "input/research_task.v1.json"
    original = contract.read_bytes()
    contract.unlink()
    try:
        stopped = service.stop("job1")
        assert stopped.stop_requested and stopped.budget_state == "unknown"
        actual = _wait(service, raw=True)
        assert actual.status == "cancelled"
    finally:
        contract.write_bytes(original)
    reconciled = service.status("job1")
    assert reconciled.status == "cancelled" and reconciled.budget_state == "retained"
    assert service.ledger.snapshot().used["training_process_us"] == 3_000_000
    assert service.ledger.snapshot().active_jobs == 0


def test_job_identity_tamper_retains_quota_and_never_resubmits(tmp_path: Path) -> None:
    service, scope, _ = _setup(tmp_path)
    _submit(service, scope)
    _wait(service, raw=True)
    path = service.root / "execution/local_jobs/job1/submission.json"
    raw = json.loads(path.read_text())
    raw["spec"]["attempt_id"] = "foreign_attempt"
    path.write_text(json.dumps(raw))
    assert service.status("job1").status == "unknown"
    assert _submit(service, scope).status == "unknown"
    assert service.ledger.snapshot().active_jobs == 1


def test_same_real_failed_command_cannot_replay_by_renaming_identity(tmp_path: Path) -> None:
    service, scope, _ = _setup(tmp_path, script="raise SystemExit(7)\n",
        budget_changes={"operation_retries": 0, "repeated_error_limit": 1})
    _submit(service, scope)
    assert _wait(service).status == "failed"
    with pytest.raises(ValueError):
        _submit(service, scope, _request("job2", attempt_id="renamed_attempt", experiment_id="renamed_experiment"))
    assert not (service.root / "execution/local_jobs/job2").exists()
    with service.ledger.journal.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM research_jobs").fetchone() == (1,)


def test_direct_runner_absolute_cutoff_and_legacy_spec_hash_remain_readable(tmp_path: Path) -> None:
    root = tmp_path / "run_absolute"
    root.mkdir()
    code = tmp_path / "code"
    code.mkdir()
    (code / "command.py").write_text("import time\ntime.sleep(30)\n")
    runner = LocalRunner(root)
    spec = LocalJobSpec(run_id=root.name, attempt_id="attempt", job_id="job", experiment_id="experiment", project="cpu",
        argv=(sys.executable, "command.py"), cwd=str(code), timeout_seconds=20.0,
        not_after_epoch_seconds=time.time() + 0.5, max_output_bytes=1024, required_metrics=("mse",), steps=1)
    runner.submit(spec)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        actual = runner.status("job")
        if actual.receipt:
            break
        time.sleep(0.03)
    assert actual.receipt is not None and actual.receipt["error"] == "deadline_exceeded"
    assert actual.deadline_at == spec.not_after_epoch_seconds
    assert actual.receipt["duration_seconds"] < 2
    # The original v1 body omits the new optional field rather than changing hashes.
    legacy = spec.model_copy(update={"job_id": "legacy", "not_after_epoch_seconds": None, "timeout_seconds": 0.1})
    runner.submit(legacy)
    body = json.loads((root / "execution/local_jobs/legacy/submission.json").read_text())
    assert "not_after_epoch_seconds" not in body["spec"]
    assert runner.status("legacy").job_id == "legacy"


CHILD = '''import json, sys, time
from pathlib import Path
from app.bridge.research_run_service import load_run_research_contract
from app.bridge.research_project_scope import prepare_project_scope
from app.bridge.research_job_service import ResearchJobService, ResearchJobRequest, ResearchJobRejected, load_research_job_policy
from app.harness.discovery.snapshots import SnapshotPolicy
from app.harness.runtime.project_scope import bind_project_scope
from app.harness.runtime.research_execution_scope import ResearchExecutionScope, bind_research_execution
from app.harness.runtime.research_budget_ledger import ResearchBudgetLedger
from app.harness.runtime.state_journal import StateJournal
from app.storage.run_store import RunStore
run = RunStore(Path(sys.argv[1])).get(sys.argv[2])
frozen = load_run_research_contract(run)
journal = StateJournal.from_authority(run.root, run_id=run.run_id)
ledger = ResearchBudgetLedger(journal, task_sha256=frozen.task_sha256, budget=frozen.task.budget)
scope = prepare_project_scope(run, candidate_id='candidate', snapshot_policy=SnapshotPolicy(allowed_paths=('*',)))
service = ResearchJobService(run, ledger, load_research_job_policy())
request = ResearchJobRequest(job_id=sys.argv[3], attempt_id='attempt1', experiment_id=sys.argv[3], command_name='train', steps=1, seed=int(sys.argv[6]) if sys.argv[6] else None)
try:
    with bind_project_scope(scope), bind_research_execution(ResearchExecutionScope(ledger, 'execution', 'subprocess-owner')):
        value = service.submit(request, scope)
    result = 'submitted'
except ResearchJobRejected as exc:
    if 'budget refused' not in str(exc):
        raise
    result = 'rejected'
Path(sys.argv[4]).write_text(result)
if sys.argv[5] == 'hold':
    time.sleep(60)
'''


def _launcher(service: ResearchJobService, marker: Path, job: str = "job1", *, hold: bool = True,
              seed: int | None = None) -> subprocess.Popen[str]:
    backend = Path(__file__).resolve().parents[2]
    return subprocess.Popen([sys.executable, "-c", CHILD, str(service.root.parent), service.run.run_id,
        job, str(marker), "hold" if hold else "exit", str(seed) if seed is not None else ""],
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(backend)},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def _await_file(path: Path, process: subprocess.Popen[str], *, timeout: float = 10) -> None:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if path.is_file():
            return
        if process.poll() is not None:
            raise AssertionError(process.communicate()[1])
        time.sleep(0.02)
    raise AssertionError("Real subprocess did not reach the expected observation")


def test_killed_dispatcher_after_sql_commit_cannot_replay_unlaunched_job(tmp_path: Path) -> None:
    service, scope, _ = _setup(tmp_path)
    directory = service.runner._directory("job1", create=True)
    marker = tmp_path / "submitted"
    with FileLock(directory / "control.lock"):
        process = _launcher(service, marker)
        try:
            until = time.monotonic() + 10
            found = False
            while time.monotonic() < until:
                with service.ledger.journal.connection() as connection:
                    found = connection.execute("SELECT COUNT(*) FROM research_jobs").fetchone() == (1,)
                if found:
                    break
                if process.poll() is not None:
                    raise AssertionError(process.communicate()[1])
                time.sleep(0.02)
            assert found
            assert not (directory / "submission.json").exists()
            process.send_signal(signal.SIGKILL)
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if process.stderr is not None:
                process.stderr.close()
    assert service.status("job1").budget_state == "unknown"
    assert _submit(service, scope).status == "unknown"
    assert service.ledger.snapshot().active_jobs == 1
    assert not (directory / "submission.json").exists() and not marker.exists()


def test_real_dispatcher_death_leaves_independent_deadline_and_receipt(tmp_path: Path) -> None:
    script = "import os, time\nfrom pathlib import Path\nPath(os.environ['MARS_RESULT_PATH']).with_name('started.pid').write_text(str(os.getpid()))\ntime.sleep(30)\n"
    service, _scope, _ = _setup(tmp_path, script=script, budget_changes={"training_job_seconds": 1})
    marker = tmp_path / "submitted"
    process = _launcher(service, marker)
    try:
        _await_file(marker, process)
        _await_file(service.root / "execution/local_jobs/job1/started.pid", process)
        process.send_signal(signal.SIGKILL)
        process.wait(timeout=5)
        result = _wait(service)
        assert result.status == "failed" and result.receipt["error"] == "deadline_exceeded"
        assert service.ledger.snapshot().active_jobs == 0
        pid = int((service.root / "execution/local_jobs/job1/started.pid").read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process.stderr is not None:
            process.stderr.close()


def test_two_real_submitters_compete_for_one_global_job_slot(tmp_path: Path) -> None:
    service, _scope, _ = _setup(tmp_path, script="import time\ntime.sleep(1)\n" + MEASURE)
    markers = [tmp_path / "one", tmp_path / "two"]
    processes = [_launcher(service, marker, job=f"job{index + 1}", hold=False, seed=index + 1) for index, marker in enumerate(markers)]
    try:
        for process in processes:
            _, error = process.communicate(timeout=10)
            assert process.returncode == 0, error
        assert sorted(marker.read_text() for marker in markers) == ["rejected", "submitted"]
        winner = "job" + str(next(index + 1 for index, marker in enumerate(markers) if marker.read_text() == "submitted"))
        assert _wait(service, winner).status == "completed"
        with service.ledger.journal.connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM research_jobs").fetchone() == (1,)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
