from __future__ import annotations

import hashlib
import json
from pathlib import Path
import signal
import sys

import pytest

from app.execution.remote.records import RemoteJobRequest, RemoteJobState
from app.execution.remote.runner import (
    RemoteRunnerError,
    cancel_job,
    derive_job_id,
    fetch_job,
    readiness,
    stage_job,
    submit_job,
    work_job,
)


def _stage_request(root: Path, request: RemoteJobRequest) -> tuple[str, str]:
    job_id = derive_job_id(request.request_id)
    stage_dir = stage_job(root, job_id)
    payload = request.model_dump_json(indent=2).encode("utf-8")
    (stage_dir / "request.json").write_bytes(payload)
    return job_id, hashlib.sha256(payload).hexdigest()


def test_submit_is_idempotent_and_rejects_changed_payload(tmp_path: Path) -> None:
    root = tmp_path / "remote"
    request = RemoteJobRequest(
        request_id="request-idempotent",
        project="pimc",
        run_id="run-1",
        candidate_id="candidate-1",
        workload_argv=(sys.executable, "trusted_adapter.py"),
    )
    job_id, request_sha256 = _stage_request(root, request)

    first = submit_job(
        root,
        job_id,
        request_sha256,
        launch_worker=lambda _root, _job_id: 4321,
    )
    second = submit_job(
        root,
        job_id,
        request_sha256,
        launch_worker=lambda _root, _job_id: 9999,
    )

    assert first == second
    assert first.state == RemoteJobState.RUNNING
    assert first.worker_pid == 4321
    with pytest.raises(RemoteRunnerError, match="different request payload") as exc_info:
        submit_job(root, job_id, "0" * 64)
    assert exc_info.value.code == "idempotency_conflict"


def test_worker_collects_adapter_response_usage_and_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "remote"
    writer = tmp_path / "trusted_adapter.py"
    response = {
        "protocol": "adapter.v1",
        "request_id": "request-worker",
        "status": "ok",
        "raw_metrics": {"RES": -27.2},
        "artifacts": {
            "metric": "metric.json",
            "candidate_fingerprint": "artifact://candidate/sha256-deadbeef",
        },
        "resource_usage": {"gpu_memory_peak_mb": 1024.0},
    }
    writer.write_text(
        "from pathlib import Path\n"
        "import json\n"
        f"response = {response!r}\n"
        "Path('metric.json').write_text(json.dumps({'RES': -27.2}), encoding='utf-8')\n"
        "Path('response.json').write_text(json.dumps(response), encoding='utf-8')\n",
        encoding="utf-8",
    )
    request = RemoteJobRequest(
        request_id="request-worker",
        project="pimc",
        run_id="run-2",
        candidate_id="candidate-2",
        workload_argv=(sys.executable, str(writer)),
        heartbeat_interval_seconds=0.1,
        gpu_ids=("0", "1"),
    )
    job_id, request_sha256 = _stage_request(root, request)
    submit_job(
        root,
        job_id,
        request_sha256,
        launch_worker=lambda _root, _job_id: 4321,
    )

    completed = work_job(root, job_id, wait_for_start=False)

    assert completed.state == RemoteJobState.SUCCEEDED
    assert completed.adapter_response is not None
    assert completed.adapter_response.raw_metrics["RES"] == -27.2
    assert completed.resource_usage.adapter["gpu_memory_peak_mb"] == 1024.0
    assert completed.resource_usage.allocated_gpu_seconds > 0.0
    names = {artifact.name for artifact in completed.artifacts}
    assert {"metric", "result_manifest", "stdout_log", "stderr_log"} <= names
    assert "candidate_fingerprint" not in names
    assert completed.adapter_response.artifacts["candidate_fingerprint"].startswith("artifact://")
    assert fetch_job(root, job_id) == completed


def test_cancel_is_idempotent_and_targets_recorded_process_groups(tmp_path: Path) -> None:
    root = tmp_path / "remote"
    request = RemoteJobRequest(
        request_id="request-cancel",
        project="pimc",
        run_id="run-3",
        candidate_id="candidate-3",
        workload_argv=(sys.executable, "trusted_adapter.py"),
    )
    job_id, request_sha256 = _stage_request(root, request)
    submit_job(
        root,
        job_id,
        request_sha256,
        launch_worker=lambda _root, _job_id: 7654,
    )
    signals: list[tuple[int, int]] = []

    cancelled = cancel_job(
        root,
        job_id,
        kill_process_group=lambda pid, sig: signals.append((pid, sig)),
    )
    repeated = cancel_job(
        root,
        job_id,
        kill_process_group=lambda pid, sig: signals.append((pid, sig)),
    )

    assert cancelled.state == RemoteJobState.CANCELLED
    assert repeated == cancelled
    assert signals == [(7654, signal.SIGTERM)]


def test_readiness_creates_only_scoped_remote_root(tmp_path: Path) -> None:
    root = tmp_path / "remote"

    result = readiness(root)

    assert result.status == "ready"
    assert result.runner_version == "remote_job.v1"
    assert root.is_dir()


def test_worker_fails_closed_when_result_request_id_changes(tmp_path: Path) -> None:
    root = tmp_path / "remote"
    writer = tmp_path / "bad_adapter.py"
    writer.write_text(
        "from pathlib import Path\n"
        "import json\n"
        "Path('response.json').write_text(json.dumps({"
        "'protocol':'adapter.v1','request_id':'wrong','status':'ok'}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    request = RemoteJobRequest(
        request_id="request-bad-result",
        project="pimc",
        run_id="run-4",
        candidate_id="candidate-4",
        workload_argv=(sys.executable, str(writer)),
        heartbeat_interval_seconds=0.1,
    )
    job_id, request_sha256 = _stage_request(root, request)
    submit_job(
        root,
        job_id,
        request_sha256,
        launch_worker=lambda _root, _job_id: 4321,
    )

    failed = work_job(root, job_id, wait_for_start=False)

    assert failed.state == RemoteJobState.FAILED
    assert failed.error_code == "result_request_mismatch"
    assert json.loads((root / "jobs" / job_id / "record.json").read_text())["state"] == "failed"
