"""Detached local supervisor; only this process signals its own child group."""
from __future__ import annotations

# -I plus this trusted file makes source-checkout launches independent of the
# caller's PYTHONPATH, current directory, and later death.
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
from typing import Any

from filelock import FileLock, Timeout as FileLockTimeout

from app.execution.local.runner import LocalRunner, _check_files, _digest, _read_json, _submission
from app.harness.persistence import atomic_write_json, path_lock
from app.harness.tools.execution.local_command import _file_hash, _read_result
from app.harness.tools.process_runtime import sanitized_subprocess_environment


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def supervise(directory: Path) -> None:
    runner = LocalRunner(directory.parents[2])
    if runner._directory(directory.name) != directory:
        raise ValueError("worker directory identity mismatch")
    lease = FileLock(directory / "worker.lock", timeout=0)
    try:
        lease.acquire()
    except FileLockTimeout:
        return
    try:
        _supervise_owned(directory)
    finally:
        lease.release()


def _supervise_owned(directory: Path) -> None:
    with path_lock(directory / "control.lock"):
        spec, record = _submission(directory)
        if (directory / "execution_receipt.json").exists():
            return
        state = _read_json(directory / "state.json")
        if state.get("status") != "queued":
            # Lost owner or duplicate worker: never replay an uncertain command.
            return
        request = _read_json(directory / "job.json")
        if _digest(request) != record["request_sha256"]:
            raise ValueError("local command request changed before execution")
        atomic_write_json(directory / "state.json", {"status": "running", "worker_pid": os.getpid()})
    request_hash = _file_hash(directory / "job.json")
    deadline = float(record["deadline_at"])
    monotonic_deadline = time.monotonic() + max(0.0, deadline - time.time())
    started_at, started = time.time(), time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    interrupted = False

    def cancel(_number: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    reason = ""
    status = "failed"
    metrics: dict[str, float] = {}
    curve: list[float] = []
    evidence: list[dict[str, Any]] = []
    written = 0
    command_files = []
    for index, argument in enumerate(spec.argv):
        candidate = Path(argument) if index == 0 else Path(spec.cwd) / argument
        if candidate.is_file():
            command_files.append({"path": str(candidate.resolve()), "sha256": _file_hash(candidate)})
    try:
        _check_files(directory)
        # Exclusive log creation prevents preexisting files/links being followed.
        with (directory / "stdout.log").open("xb") as output, (directory / "stderr.log").open("xb") as error:
            if (directory / "stop.json").exists():
                status, reason = "cancelled", "stop_requested"
            elif time.monotonic() >= monotonic_deadline:
                reason = "deadline_exceeded"
            else:
                environment = sanitized_subprocess_environment(overrides={
                    "MARS_RUN_ID": spec.run_id, "MARS_EXPERIMENT_ID": spec.experiment_id,
                    "MARS_PROJECT": spec.project, "MARS_RUN_ROOT": record["run_root"],
                    "MARS_JOB_REQUEST": str(directory / "job.json"), "MARS_RESULT_PATH": str(directory / "result.json"),
                    # CPU is the sole capability of this runner, not an OS sandbox.
                    "CUDA_VISIBLE_DEVICES": "", "HIP_VISIBLE_DEVICES": "", "ROCR_VISIBLE_DEVICES": "",
                })
                process = subprocess.Popen(spec.argv, cwd=spec.cwd, env=environment,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    start_new_session=True, close_fds=True)
                assert process.stdout is not None and process.stderr is not None
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ, output)
                    selector.register(process.stderr, selectors.EVENT_READ, error)
                    while selector.get_map():
                        if interrupted or (directory / "stop.json").exists():
                            status, reason = "cancelled", "stop_requested"
                            break
                        if time.monotonic() >= monotonic_deadline or time.time() >= deadline:
                            reason = "deadline_exceeded"
                            break
                        for key, _ in selector.select(timeout=min(0.05, max(0.0, monotonic_deadline - time.monotonic()))):
                            chunk = os.read(key.fd, 8192)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            available = max(0, spec.max_output_bytes - written)
                            key.data.write(chunk[:available])
                            written += min(len(chunk), available)
                            if len(chunk) > available:
                                reason = "output_limit_exceeded"
                                break
                        if reason:
                            break
                if not reason:
                    # Pipes may close before the child terminates. Still supervise
                    # this period, and kill descendants even after leader exit.
                    while process.poll() is None:
                        if interrupted or (directory / "stop.json").exists():
                            status, reason = "cancelled", "stop_requested"
                            break
                        if time.monotonic() >= monotonic_deadline or time.time() >= deadline:
                            reason = "deadline_exceeded"
                            break
                        time.sleep(min(0.05, max(0.0, monotonic_deadline - time.monotonic())))
                if not reason and process.returncode != 0:
                    reason = "command_failed"
            output.flush()
            error.flush()
            os.fsync(output.fileno())
            os.fsync(error.fileno())
    except Exception:
        reason = "supervisor_execution_failed"
    finally:
        if process is not None:
            _kill_group(process)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
    duration = time.monotonic() - started
    if not reason:
        try:
            _check_files(directory)
            if _file_hash(directory / "job.json") != request_hash:
                raise ValueError("command changed its job request")
            raw = _read_json(directory / "result.json")
            for name in raw.get("evidence_paths", []):
                if not isinstance(name, str):
                    raise ValueError("invalid evidence path")
                source = directory
                for part in Path(name).parts:
                    source /= part
                    if source.is_symlink():
                        raise ValueError("evidence cannot traverse symbolic links")
            metrics, curve, evidence = _read_result(directory / "result.json", request, spec.required_metrics)
            status = "completed"
        except Exception:
            reason = "invalid_measurement_result"
    receipt: dict[str, Any] = {
        "schema": "local_command_receipt.v1", "invocation_id": spec.job_id,
        "run_id": spec.run_id, "experiment_id": spec.experiment_id, "attempt_id": spec.attempt_id,
        "job_id": spec.job_id, "project": spec.project, "status": status,
        "returncode": process.returncode if process is not None else None,
        "started_at": started_at, "finished_at": time.time(), "deadline_at": deadline,
        "duration_seconds": duration, "argv": list(spec.argv), "command_files": command_files,
        "execution_backend": "local_process", "os_isolated": False, "device": "cpu",
        "request_sha256": request_hash, "submission_sha256": record["spec_sha256"],
        "result_sha256": (_file_hash(directory / "result.json")
                          if (directory / "result.json").is_file() and not (directory / "result.json").is_symlink() else None),
        "metrics": metrics, "loss_curve": curve, "evidence": evidence,
        "error": reason, "captured_output_bytes": written, "max_output_bytes": spec.max_output_bytes,
    }
    with path_lock(directory / "control.lock"):
        _check_files(directory)
        atomic_write_json(directory / "execution_receipt.json", receipt)
        atomic_write_json(directory / "state.json", {"status": status})


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    try:
        supervise(Path(sys.argv[1]))
    except Exception:
        # An incomplete supervisor is observable as unknown, never success.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
