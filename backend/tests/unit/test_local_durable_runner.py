"""Real CPU programs and detached processes; no provider or service stand-ins."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from app.execution.local.runner import LocalJobSpec, LocalJobStatus, LocalRunner
from app.execution.local import worker

pytestmark = pytest.mark.skipif(os.name != "posix", reason="independent local runner currently supports POSIX")

_MEASURE = '''import json, os
from pathlib import Path
request = json.loads(Path(os.environ['MARS_JOB_REQUEST']).read_text())
root = Path(request['output_dir'])
x = [1.0, 2.0, 3.0]
y = [2.0, 4.0, 6.0]
predicted = [1.5 * value for value in x]
squared_errors = [(actual - prediction) ** 2 for actual, prediction in zip(y, predicted)]
(root / 'measurements.json').write_text(json.dumps({'x': x, 'y': y, 'predictions': predicted, 'squared_errors': squared_errors}))
Path(os.environ['MARS_RESULT_PATH']).write_text(json.dumps({'schema': 'local_command_result.v1', 'invocation_id': request['invocation_id'], 'run_id': request['run_id'], 'experiment_id': request['experiment_id'], 'status': 'completed', 'metrics': {'mse': sum(squared_errors) / len(x)}, 'evidence_paths': ['measurements.json']}))
'''


def _setup(tmp_path: Path, script: str = _MEASURE, *, timeout: float = 8.0,
           output: int = 4096) -> tuple[LocalRunner, LocalJobSpec, Path]:
    root = tmp_path / "run_local"
    root.mkdir()
    code = tmp_path / "code"
    code.mkdir()
    command = code / "command.py"
    command.write_text(script)
    spec = LocalJobSpec(run_id=root.name, attempt_id="execution_1", job_id="cpu_job_1",
        experiment_id="baseline", project="generic", argv=(sys.executable, str(command)),
        cwd=str(code), timeout_seconds=timeout, max_output_bytes=output,
        required_metrics=("mse",), steps=1)
    return LocalRunner(root), spec, command


def _wait(runner: LocalRunner, job_id: str, *, timeout: float = 12) -> LocalJobStatus:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = runner.status(job_id)
        if status.receipt is not None:
            return status
        time.sleep(0.03)
    runner.stop(job_id)
    raise AssertionError("real local job did not publish a terminal receipt")


def _wait_file(path: Path, *, timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size:
            return
        time.sleep(0.03)
    raise AssertionError("real command did not produce its startup marker")


def test_actual_measurement_reuses_command_protocol_and_receipt(tmp_path: Path) -> None:
    runner, spec, _ = _setup(tmp_path)
    admitted = runner.submit(spec)
    assert admitted.status in {"queued", "running"}
    completed = _wait(runner, spec.job_id)
    assert completed.status == "completed" and completed.receipt is not None
    receipt = completed.receipt
    assert receipt["schema"] == "local_command_receipt.v1"
    assert receipt["metrics"]["mse"] == pytest.approx(3.5 / 3)
    assert receipt["run_id"] == spec.run_id and receipt["attempt_id"] == spec.attempt_id
    assert receipt["invocation_id"] == spec.job_id and receipt["returncode"] == 0
    assert receipt["os_isolated"] is False and receipt["device"] == "cpu"
    assert len(receipt["evidence"]) == 1 and receipt["evidence"][0]["bytes"] > 0
    before = runner._directory(spec.job_id).joinpath("execution_receipt.json").read_bytes()
    assert runner.submit(spec).receipt == receipt
    assert runner.stop(spec.job_id).receipt == receipt
    assert runner._directory(spec.job_id).joinpath("execution_receipt.json").read_bytes() == before


def test_results_read_durable_measurements_without_recovering_or_mutating_job(tmp_path: Path) -> None:
    from app.bridge.results_service import ResultReader, load_results_policy
    from app.storage.run_store import RunHandle

    runner, spec, _ = _setup(tmp_path)
    runner.submit(spec)
    assert _wait(runner, spec.job_id).status == "completed"
    run = RunHandle(run_id=spec.run_id, root=runner.root, project=spec.project,
                    task="Actual durable CPU measurement", entrypoint="execution", created_at="")
    before = {p.relative_to(runner.root): p.read_bytes() for p in runner.root.rglob("*") if p.is_file()}
    reader = ResultReader(run, load_results_policy())
    experiments, metrics, _ = reader.jobs()
    assert not reader.limitations
    assert len(experiments) == 1 and experiments[0]["verification"] == "verified_local_receipt"
    assert metrics[0]["value"] == pytest.approx(3.5 / 3)
    assert {p.relative_to(runner.root): p.read_bytes() for p in runner.root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("name", ["submission.json", "state.json", "worker.lock", "control.lock"])
def test_control_files_are_not_measurement_evidence(tmp_path: Path, name: str) -> None:
    runner, spec, _ = _setup(tmp_path, _MEASURE.replace("['measurements.json']", repr([name])))
    runner.submit(spec)
    completed = _wait(runner, spec.job_id)
    assert completed.status == "failed" and completed.receipt is not None
    assert completed.receipt["error"] == "invalid_measurement_result"
    assert not completed.receipt["metrics"]


def test_damaged_failed_receipt_cannot_promote_job_to_completed(tmp_path: Path) -> None:
    runner, spec, _ = _setup(tmp_path, "raise SystemExit(3)\n")
    runner.submit(spec)
    assert _wait(runner, spec.job_id).status == "failed"
    path = runner._directory(spec.job_id) / "execution_receipt.json"
    receipt = json.loads(path.read_text())
    receipt["status"] = "completed"
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="completed receipt is inconsistent"):
        runner.status(spec.job_id)


def test_measurement_change_invalidates_completed_job_status(tmp_path: Path) -> None:
    runner, spec, _ = _setup(tmp_path)
    runner.submit(spec)
    assert _wait(runner, spec.job_id).status == "completed"
    (runner._directory(spec.job_id) / "measurements.json").write_text("Damaged actual measurement")
    with pytest.raises(ValueError, match="measurement receipt is inconsistent"):
        runner.status(spec.job_id)


def test_results_reject_control_file_even_if_corrupt_receipt_hashes_match(tmp_path: Path) -> None:
    from app.bridge.results_service import ResultReader, load_results_policy
    from app.harness.tools.execution.local_command import _file_hash
    from app.storage.run_store import RunHandle

    runner, spec, _ = _setup(tmp_path)
    runner.submit(spec)
    assert _wait(runner, spec.job_id).status == "completed"
    directory = runner._directory(spec.job_id)
    result_path, receipt_path = directory / "result.json", directory / "execution_receipt.json"
    result = json.loads(result_path.read_text())
    result["evidence_paths"] = ["submission.json"]
    result_path.write_text(json.dumps(result))
    receipt = json.loads(receipt_path.read_text())
    receipt["result_sha256"] = _file_hash(result_path)
    control_file = directory / "submission.json"
    receipt["evidence"] = [{"kind": "measurement_evidence", "path": str(control_file),
                            "sha256": _file_hash(control_file), "bytes": control_file.stat().st_size}]
    receipt_path.write_text(json.dumps(receipt))
    run = RunHandle(run_id=spec.run_id, root=runner.root, project=spec.project, task="Tampered evidence rejection",
                    entrypoint="execution", created_at="")
    experiments, metrics, _ = ResultReader(run, load_results_policy()).jobs()
    assert experiments[0]["verification"] == "invalid" and not metrics


@pytest.mark.parametrize("field,value", [("attempt_id", "another_attempt"), ("job_id", "other_job"),
    ("submission_sha256", "sha256:" + "0" * 64), ("deadline_at", 0.0)])
def test_results_reject_durable_identity_or_deadline_damage(tmp_path: Path, field: str, value: object) -> None:
    from app.bridge.results_service import ResultReader, load_results_policy
    from app.storage.run_store import RunHandle

    runner, spec, _ = _setup(tmp_path)
    runner.submit(spec)
    assert _wait(runner, spec.job_id).status == "completed"
    path = runner._directory(spec.job_id) / "execution_receipt.json"
    receipt = json.loads(path.read_text())
    receipt[field] = value
    path.write_text(json.dumps(receipt))
    run = RunHandle(run_id=spec.run_id, root=runner.root, project=spec.project, task="Integrity check",
                    entrypoint="execution", created_at="")
    reader = ResultReader(run, load_results_policy())
    experiments, metrics, curves = reader.jobs()
    assert len(experiments) == 1 and experiments[0]["verification"] == "invalid"
    assert not metrics and not curves
    assert reader.limitations


def test_duplicate_submit_and_duplicate_worker_do_not_execute_twice(tmp_path: Path) -> None:
    script = "from pathlib import Path\nimport time\nwith Path('starts').open('a') as f: f.write('started\\n')\ntime.sleep(0.4)\n" + _MEASURE
    runner, spec, command = _setup(tmp_path, script)
    runner.submit(spec)
    _wait_file(command.parent / "starts")
    assert runner.status(spec.job_id).owner_active
    runner.submit(spec)
    duplicate = subprocess.run([sys.executable, "-I", str(Path(worker.__file__)),
        str(runner._directory(spec.job_id))], capture_output=True, timeout=10)
    assert duplicate.returncode == 0
    assert _wait(runner, spec.job_id).status == "completed"
    assert (command.parent / "starts").read_text() == "started\n"
    with pytest.raises(ValueError, match="different submission"):
        runner.submit(spec.model_copy(update={"steps": 2}))


def test_deadline_terminates_real_command_and_descendant_group(tmp_path: Path) -> None:
    script = r'''import subprocess, sys, time
from pathlib import Path
child = "from pathlib import Path; import time; p=Path('child_ticks'); " + "\nwhile True:\n p.write_text(str(time.monotonic()))\n time.sleep(0.02)"
subprocess.Popen([sys.executable, '-c', child])
Path('started').write_text('yes')
time.sleep(30)
'''
    runner, spec, command = _setup(tmp_path, script, timeout=2.5)
    runner.submit(spec)
    _wait_file(command.parent / "child_ticks")
    receipt = _wait(runner, spec.job_id).receipt
    assert receipt and receipt["status"] == "failed" and receipt["error"] == "deadline_exceeded"
    assert receipt["returncode"] == -signal.SIGKILL
    ticks = (command.parent / "child_ticks").read_bytes()
    time.sleep(0.15)
    assert (command.parent / "child_ticks").read_bytes() == ticks


def test_stop_uses_live_owner_and_is_idempotent(tmp_path: Path) -> None:
    runner, spec, command = _setup(tmp_path, "from pathlib import Path\nimport time\nPath('started').write_text('yes')\ntime.sleep(30)\n")
    runner.submit(spec)
    _wait_file(command.parent / "started")
    requested = runner.stop(spec.job_id)
    assert requested.stop_requested
    runner.stop(spec.job_id)
    stopped = _wait(runner, spec.job_id)
    assert stopped.status == "cancelled" and stopped.receipt
    assert stopped.receipt["error"] == "stop_requested"
    assert stopped.receipt["returncode"] == -signal.SIGKILL


def test_launching_backend_sigkill_does_not_remove_worker_deadline(tmp_path: Path) -> None:
    runner, spec, command = _setup(tmp_path, "from pathlib import Path\nimport time\nPath('started').write_text('yes')\ntime.sleep(30)\n", timeout=3.0)
    spec_path = tmp_path / "input.json"
    spec_path.write_text(spec.model_dump_json())
    launcher = tmp_path / "backend.py"
    launcher.write_text("import sys, time\nfrom pathlib import Path\nsys.path.insert(0, "
        + repr(str(Path(worker.__file__).resolve().parents[3])) + ")\n"
        + "from app.execution.local import LocalRunner, LocalJobSpec\n"
        + "LocalRunner(Path(sys.argv[1])).submit(LocalJobSpec.model_validate_json(Path(sys.argv[2]).read_text()))\n"
        + "time.sleep(30)\n")
    backend = subprocess.Popen([sys.executable, "-I", str(launcher), str(runner.root), str(spec_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
    try:
        _wait_file(command.parent / "started")
        assert backend.poll() is None and runner.status(spec.job_id).owner_active
        backend.kill()
        _, stderr = backend.communicate(timeout=5)
        assert backend.returncode == -signal.SIGKILL and not stderr
        terminal = _wait(runner, spec.job_id)
        assert terminal.receipt and terminal.receipt["error"] == "deadline_exceeded"
        assert terminal.receipt["returncode"] == -signal.SIGKILL
    finally:
        if backend.poll() is None:
            backend.kill()
            backend.communicate(timeout=5)
        if (runner.root / "execution/local_jobs" / spec.job_id).exists():
            runner.stop(spec.job_id)


def test_combined_output_limit_stops_command_and_bounds_saved_logs(tmp_path: Path) -> None:
    runner, spec, _ = _setup(tmp_path,
        "import os, time\nos.write(1, b'x' * 4096)\nos.write(2, b'y' * 4096)\ntime.sleep(30)\n", output=1000)
    runner.submit(spec)
    result = _wait(runner, spec.job_id)
    assert result.receipt and result.receipt["error"] == "output_limit_exceeded"
    directory = runner._directory(spec.job_id)
    assert sum((directory / name).stat().st_size for name in ("stdout.log", "stderr.log")) == 1000
    assert result.receipt["captured_output_bytes"] == 1000


@pytest.mark.parametrize("script,reason", [("raise SystemExit(3)\n", "command_failed"),
    ("pass\n", "invalid_measurement_result"),
    (_MEASURE.replace("'run_id': request['run_id']", "'run_id': 'wrong_run'"), "invalid_measurement_result")])
def test_exit_zero_and_wrong_identity_do_not_invent_measurements(tmp_path: Path, script: str, reason: str) -> None:
    runner, spec, _ = _setup(tmp_path, script)
    runner.submit(spec)
    result = _wait(runner, spec.job_id)
    assert result.status == "failed" and result.receipt
    assert result.receipt["error"] == reason and result.receipt["metrics"] == {}


def test_control_symlink_is_rejected_without_external_write(tmp_path: Path) -> None:
    runner, spec, _ = _setup(tmp_path)
    directory = runner._directory(spec.job_id, create=True)
    external = tmp_path / "external.json"
    external.write_text("untouched")
    (directory / "state.json").symlink_to(external)
    with pytest.raises(ValueError, match="plain file"):
        runner.submit(spec)
    assert external.read_text() == "untouched"


def test_unknown_worker_outcome_never_relaunches_same_job(tmp_path: Path) -> None:
    runner, spec, _ = _setup(tmp_path)
    runner.submit(spec)
    completed = _wait(runner, spec.job_id)
    assert completed.receipt
    directory = runner._directory(spec.job_id)
    (directory / "execution_receipt.json").unlink()
    (directory / "state.json").write_text(json.dumps({"status": "running"}))
    deadline = time.monotonic() + 3
    while runner.status(spec.job_id).owner_active and time.monotonic() < deadline:
        time.sleep(0.03)
    assert runner.status(spec.job_id).status == "unknown"
    assert runner.submit(spec).status == "unknown"
    assert not (directory / "execution_receipt.json").exists()


def test_concurrent_submitters_share_one_actual_execution(tmp_path: Path) -> None:
    script = "from pathlib import Path\nimport time\nwith Path('starts').open('a') as f: f.write('started\\n')\ntime.sleep(0.4)\n" + _MEASURE
    runner, spec, command = _setup(tmp_path, script)
    spec_path = tmp_path / "submit.json"
    spec_path.write_text(spec.model_dump_json())
    launcher = tmp_path / "submit.py"
    launcher.write_text("import sys\nfrom pathlib import Path\nsys.path.insert(0, "
        + repr(str(Path(worker.__file__).resolve().parents[3])) + ")\n"
        + "from app.execution.local import LocalRunner, LocalJobSpec\n"
        + "LocalRunner(Path(sys.argv[1])).submit(LocalJobSpec.model_validate_json(Path(sys.argv[2]).read_text()))\n")
    processes = [subprocess.Popen([sys.executable, "-I", str(launcher), str(runner.root), str(spec_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0 and not stdout and not stderr
    assert _wait(runner, spec.job_id).status == "completed"
    assert (command.parent / "starts").read_text() == "started\n"


@pytest.mark.parametrize("target", ["root", "stage", "job"])
def test_linked_run_or_job_directories_are_rejected(tmp_path: Path, target: str) -> None:
    runner, spec, _ = _setup(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    if target == "root":
        link = tmp_path / "linked_run"
        link.symlink_to(runner.root, target_is_directory=True)
        with pytest.raises(ValueError, match="plain directory"):
            LocalRunner(link)
    else:
        (runner.root / "execution").mkdir()
        link = runner.root / "execution/local_jobs"
        if target == "job":
            link.mkdir()
            link = link / spec.job_id
        link.symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match="symbolic link"):
            runner.submit(spec)
    assert list(outside.iterdir()) == []


def test_changed_deadline_is_not_admitted_as_original_submission(tmp_path: Path) -> None:
    runner, spec, _ = _setup(tmp_path)
    runner.submit(spec)
    assert _wait(runner, spec.job_id).status == "completed"
    path = runner._directory(spec.job_id) / "submission.json"
    payload = json.loads(path.read_text())
    payload["deadline_at"] += 100
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        runner.status(spec.job_id)


def test_status_probe_cannot_be_mistaken_for_a_live_worker(tmp_path: Path) -> None:
    """Hold the actual short status-probe lease while real workers start."""
    from filelock import FileLock
    from app.harness.persistence import path_lock

    script = "from pathlib import Path\nwith Path('starts').open('a') as f: f.write('started\\n')\n" + _MEASURE
    runner, spec, command = _setup(tmp_path, script)
    directory = runner._directory(spec.job_id, create=True)
    ready = tmp_path / "worker_imported"
    helper = "\n".join([
        "from pathlib import Path", "from app.execution.local.worker import supervise",
        "import sys", "Path(sys.argv[2]).write_text('ready')", "supervise(Path(sys.argv[1]))",
    ])
    process = None
    try:
        # status() already uses this order: control lock, then probe worker.lock.
        with path_lock(directory / "control.lock"), FileLock(directory / "worker.lock"):
            runner.submit(spec)
            process = subprocess.Popen([sys.executable, "-c", helper, str(directory), str(ready)])
            _wait_file(ready)
            # Import is finished: the real supervisor must wait for the probe's
            # control transaction, not decide this temporary lease is an owner.
            with pytest.raises(subprocess.TimeoutExpired):
                process.wait(timeout=0.25)
        assert _wait(runner, spec.job_id).status == "completed"
        assert process.wait(timeout=8) == 0
        assert (command.parent / "starts").read_text() == "started\n"
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=8)
        runner.stop(spec.job_id)
