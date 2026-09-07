"""Real child processes, file hashing, cancellation and manifest rejection; no invented GPU jobs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time
from collections.abc import Iterator
import pytest
from app.execution.remote.records import RemoteJobRequest, RemoteJobState, RemoteJobRecord
from app.execution.remote.runner import (
    RemoteRunnerError, cancel_job, derive_job_id, fetch_job, readiness,
    stage_job, submit_job, _read_adapter_response,
)


@pytest.fixture(autouse=True)
def actual_worker_import_path(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    yield


def stage_request(root: Path, request: RemoteJobRequest) -> tuple[str, str]:
    job_id = derive_job_id(request.request_id)
    stage = stage_job(root, job_id)
    payload = request.model_dump_json(indent=2).encode()
    (stage / "request.json").write_bytes(payload)
    return job_id, hashlib.sha256(payload).hexdigest()


def wait_terminal(root: Path, job_id: str, *, seconds: float = 8) -> RemoteJobRecord:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        record = fetch_job(root, job_id)
        if record.state.terminal:
            return record
        time.sleep(0.05)
    cancel_job(root, job_id)
    pytest.fail("actual worker did not terminate within its test deadline")


def hash_workload(tmp_path: Path) -> Path:
    script = tmp_path / "hash_request.py"
    script.write_text(
        "import hashlib,json\nfrom pathlib import Path\n"
        "data=Path('request.json').read_bytes()\n"
        "sha=hashlib.sha256(data).hexdigest()\n"
        "Path('request.sha256').write_text(sha)\n"
        "Path('response.json').write_text(json.dumps({'protocol':'adapter.v1',"
        "'request_id':json.loads(data)['request_id'],'status':'ok',"
        "'raw_metrics':{'input_bytes':len(data)},'artifacts':{'hash':'request.sha256'}}))\n"
    )
    return script


def test_real_submit_is_idempotent_and_rejects_changed_payload(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    request = RemoteJobRequest(request_id="actual-idempotence", project="pimc", run_id="file-contract",
        candidate_id="file-hash", workload_argv=(sys.executable, str(hash_workload(tmp_path))), heartbeat_interval_seconds=0.1)
    job_id, sha = stage_request(root, request)
    first = submit_job(root, job_id, sha)
    try:
        second = submit_job(root, job_id, sha)
        assert first.worker_pid == second.worker_pid and first.job_id == second.job_id
        assert first.worker_pid is not None and first.worker_pid != os.getpid()
        with pytest.raises(RemoteRunnerError, match="different request payload"):
            submit_job(root, job_id, "0"*64)
        final = wait_terminal(root, job_id)
        assert final.state == RemoteJobState.SUCCEEDED
        assert (root / "jobs" / job_id / "request.sha256").read_text() == sha
        assert final.adapter_response is not None and final.adapter_response.raw_metrics["input_bytes"] > 0
        assert final.resource_usage.allocated_gpu_seconds == 0
        assert {"hash", "result_manifest", "stdout_log", "stderr_log"} <= {a.name for a in final.artifacts}
        assert fetch_job(root, job_id) == final
    finally:
        cancel_job(root, job_id)


def test_cancel_terminates_an_actual_owned_process_group_and_is_idempotent(tmp_path: Path) -> None:
    # This is a process-control workload; no simulated scientific result is produced.
    script = tmp_path / "wait_for_signal.py"
    script.write_text("import signal\nsignal.pause()\n")
    request = RemoteJobRequest(request_id="actual-cancel", project="pimc", run_id="process-contract",
        candidate_id="signal-waiter", workload_argv=(sys.executable, str(script)), heartbeat_interval_seconds=0.1)
    job_id, sha = stage_request(tmp_path / "remote", request)
    root = tmp_path / "remote"
    submit_job(root, job_id, sha)
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and fetch_job(root, job_id).workload_pid is None:
            time.sleep(0.05)
        record = fetch_job(root, job_id)
        assert record.workload_pid is not None
        cancelled = cancel_job(root, job_id)
        assert cancelled.state == RemoteJobState.CANCELLED
        assert cancel_job(root, job_id) == cancelled
        # The kernel must have killed/stopped this process; a zombie has exited.
        proc = Path(f"/proc/{record.workload_pid}/stat")
        deadline = time.monotonic() + 3
        while proc.exists() and proc.read_text().split()[2] != "Z" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not proc.exists() or proc.read_text().split()[2] == "Z"
    finally:
        cancel_job(root, job_id)


def test_readiness_creates_only_scoped_remote_root(tmp_path: Path) -> None:
    root = tmp_path / "remote"
    result = readiness(root)
    assert result.status == "ready" and result.runner_version == "remote_job.v1"
    assert root.is_dir()


def test_result_manifest_rejects_a_different_request_identity(tmp_path: Path) -> None:
    # Manually authored malformed parser input; no successful service run is claimed.
    request = RemoteJobRequest(request_id="expected", project="pimc", run_id="parser-contract",
        candidate_id="none", workload_argv=(sys.executable,))
    (tmp_path / "response.json").write_text(json.dumps({"protocol":"adapter.v1", "request_id":"wrong", "status":"ok"}))
    with pytest.raises(RemoteRunnerError) as caught:
        _read_adapter_response(tmp_path, request)
    assert caught.value.code == "result_request_mismatch"


def test_actual_missing_program_is_recorded_as_workload_failure(tmp_path: Path) -> None:
    root = tmp_path / "remote"
    request = RemoteJobRequest(request_id="actual-program-failure", project="pimc", run_id="process-contract",
        candidate_id="missing", workload_argv=(sys.executable, str(tmp_path / "absent.py")), heartbeat_interval_seconds=0.1)
    job_id, sha = stage_request(root, request)
    submit_job(root, job_id, sha)
    try:
        failed = wait_terminal(root, job_id)
        assert failed.state == RemoteJobState.FAILED and failed.error_code == "workload_failed"
        assert failed.adapter_response is None and failed.exit_code != 0
    finally:
        cancel_job(root, job_id)
