"""Fixed remote entrypoint for durable GPU jobs.

The SSH client invokes only this module.  Workload argv and candidate payloads
are read from a hash-verified JSON manifest inside the remote job directory.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Any, NoReturn

from pydantic import BaseModel

from app.execution.adapters.base import AdapterResponse
from app.execution.remote.records import (
    RemoteJobRecord,
    RemoteJobRequest,
    RemoteJobState,
    RemoteOutputArtifact,
    RemoteReadiness,
    RemoteResourceUsage,
    derive_remote_job_id,
    validate_identifier,
)

RUNNER_VERSION = "remote_job.v1"
_REQUEST_FILE = "request.json"
_RECORD_FILE = "record.json"
_START_FILE = ".start"


class RemoteRunnerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def readiness(root: Path) -> RemoteReadiness:
    try:
        _normalized_root(root).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return RemoteReadiness(
            status="blocked",
            runner_version=RUNNER_VERSION,
            error_code="remote_root_unavailable",
            error=str(exc),
        )
    return RemoteReadiness(status="ready", runner_version=RUNNER_VERSION)


def stage_job(root: Path, job_id: str) -> Path:
    normalized_id = validate_identifier(job_id, label="job_id")
    stage_dir = _normalized_root(root) / "incoming" / normalized_id
    (stage_dir / "inputs").mkdir(parents=True, exist_ok=True)
    return stage_dir


def submit_job(
    root: Path,
    job_id: str,
    request_sha256: str,
    *,
    launch_worker: Callable[[Path, str], int] | None = None,
) -> RemoteJobRecord:
    normalized_id = validate_identifier(job_id, label="job_id")
    normalized_root = _normalized_root(root)
    job_dir = normalized_root / "jobs" / normalized_id
    existing_path = job_dir / _RECORD_FILE
    if existing_path.is_file():
        existing = _read_record(existing_path)
        if existing.request_sha256 != request_sha256:
            raise RemoteRunnerError(
                "idempotency_conflict",
                "job_id already exists with a different request payload",
            )
        return existing

    stage_dir = normalized_root / "incoming" / normalized_id
    request_path = stage_dir / _REQUEST_FILE
    if not request_path.is_file():
        raise RemoteRunnerError("request_missing", "staged request.json is missing")
    actual_sha256 = _sha256_file(request_path)
    if actual_sha256 != request_sha256:
        raise RemoteRunnerError(
            "request_hash_mismatch",
            "staged request.json failed SHA-256 verification",
        )
    request = RemoteJobRequest.model_validate_json(request_path.read_text(encoding="utf-8"))
    expected_job_id = derive_remote_job_id(request.request_id)
    if normalized_id != expected_job_id:
        raise RemoteRunnerError(
            "job_id_mismatch",
            f"expected deterministic job_id {expected_job_id!r}",
        )
    _verify_input_artifacts(stage_dir, request)

    job_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        stage_dir.rename(job_dir)
    except FileExistsError:
        if existing_path.is_file():
            existing = _read_record(existing_path)
            if existing.request_sha256 == request_sha256:
                return existing
        raise RemoteRunnerError("job_directory_conflict", "job directory already exists")

    submitted_at = _utcnow()
    queued = RemoteJobRecord(
        request_id=request.request_id,
        job_id=normalized_id,
        request_sha256=request_sha256,
        state=RemoteJobState.QUEUED,
        submitted_at=submitted_at,
        heartbeat_at=submitted_at,
    )
    _write_record(existing_path, queued)
    launcher = launch_worker or _launch_worker
    try:
        worker_pid = launcher(normalized_root, normalized_id)
    except OSError as exc:
        failed = queued.model_copy(
            update={
                "state": RemoteJobState.FAILED,
                "finished_at": _utcnow(),
                "error_code": "worker_launch_failed",
                "error": str(exc),
            }
        )
        _write_record(existing_path, failed)
        return failed

    running = queued.model_copy(
        update={
            "state": RemoteJobState.RUNNING,
            "started_at": _utcnow(),
            "heartbeat_at": _utcnow(),
            "worker_pid": worker_pid,
        }
    )
    _write_record(existing_path, running)
    (job_dir / _START_FILE).write_text("ready\n", encoding="utf-8")
    return running


def status_job(root: Path, job_id: str) -> RemoteJobRecord:
    return _read_record(_job_dir(root, job_id) / _RECORD_FILE)


def cancel_job(
    root: Path,
    job_id: str,
    *,
    kill_process_group: Callable[[int, int], None] | None = None,
) -> RemoteJobRecord:
    record_path = _job_dir(root, job_id) / _RECORD_FILE
    record = _read_record(record_path)
    if record.state.terminal:
        return record
    cancelled = record.model_copy(
        update={
            "state": RemoteJobState.CANCELLED,
            "finished_at": _utcnow(),
            "heartbeat_at": _utcnow(),
            "error_code": "cancelled_by_request",
            "error": "remote job cancelled",
        }
    )
    _write_record(record_path, cancelled)
    killer = kill_process_group or _kill_process_group
    for pid in (record.workload_pid, record.worker_pid):
        if pid is None:
            continue
        try:
            killer(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            raise RemoteRunnerError("cancel_permission_denied", str(exc)) from exc
    return cancelled


def fetch_job(root: Path, job_id: str) -> RemoteJobRecord:
    """Return a record whose artifact paths can be pulled by the SSH client."""

    return status_job(root, job_id)


def work_job(root: Path, job_id: str, *, wait_for_start: bool = True) -> RemoteJobRecord:
    job_dir = _job_dir(root, job_id)
    if wait_for_start:
        _wait_for_start(job_dir / _START_FILE)
    record_path = job_dir / _RECORD_FILE
    record = _read_record(record_path)
    request = RemoteJobRequest.model_validate_json(
        (job_dir / _REQUEST_FILE).read_text(encoding="utf-8")
    )
    _verify_input_artifacts(job_dir, request)
    started_at = record.started_at or _utcnow()
    record = record.model_copy(
        update={
            "state": RemoteJobState.RUNNING,
            "started_at": started_at,
            "heartbeat_at": _utcnow(),
            "worker_pid": os.getpid(),
            "heartbeat_stale": False,
        }
    )
    _write_record(record_path, record)

    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    environment = dict(os.environ)
    if request.gpu_ids:
        environment["CUDA_VISIBLE_DEVICES"] = ",".join(request.gpu_ids)
    monotonic_started = time.monotonic()
    try:
        with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
            process = _spawn_workload_process(
                argv=list(request.workload_argv),
                cwd=job_dir,
                environment=environment,
                stdout_handle=stdout_handle,
                stderr_handle=stderr_handle,
            )
            record = record.model_copy(update={"workload_pid": process.pid})
            _write_record(record_path, record)
            timed_out = False
            while process.poll() is None:
                current = _read_record(record_path)
                if current.state == RemoteJobState.CANCELLED:
                    _terminate_process_group(process.pid)
                    process.wait(timeout=10.0)
                    return current
                elapsed = time.monotonic() - monotonic_started
                if elapsed > request.timeout_seconds:
                    timed_out = True
                    _terminate_process_group(process.pid)
                    break
                record = current.model_copy(
                    update={
                        "heartbeat_at": _utcnow(),
                        "heartbeat_stale": False,
                        "resource_usage": _resource_usage(request, elapsed),
                    }
                )
                _write_record(record_path, record)
                time.sleep(request.heartbeat_interval_seconds)
            try:
                returncode = process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                process.kill()
                returncode = process.wait(timeout=10.0)
    except OSError as exc:
        return _finish_failed(
            record_path,
            record,
            request,
            monotonic_started,
            error_code="workload_launch_failed",
            error=str(exc),
        )

    elapsed = time.monotonic() - monotonic_started
    if timed_out:
        return _finish_failed(
            record_path,
            record,
            request,
            monotonic_started,
            error_code="workload_timeout",
            error=f"workload exceeded {request.timeout_seconds}s",
            exit_code=returncode,
        )
    if returncode != 0:
        return _finish_failed(
            record_path,
            record,
            request,
            monotonic_started,
            error_code="workload_failed",
            error=f"workload exited with code {returncode}",
            exit_code=returncode,
        )

    try:
        adapter_response = _read_adapter_response(job_dir, request)
        artifacts = _collect_artifacts(job_dir, request, adapter_response)
    except (OSError, ValueError, RemoteRunnerError) as exc:
        return _finish_failed(
            record_path,
            record,
            request,
            monotonic_started,
            error_code=getattr(exc, "code", "result_invalid"),
            error=str(exc),
            exit_code=returncode,
        )
    adapter_failed = adapter_response.status in {"blocked", "failed"}
    completed = record.model_copy(
        update={
            "state": RemoteJobState.FAILED if adapter_failed else RemoteJobState.SUCCEEDED,
            "heartbeat_at": _utcnow(),
            "finished_at": _utcnow(),
            "heartbeat_stale": False,
            "exit_code": returncode,
            "adapter_response": adapter_response,
            "resource_usage": RemoteResourceUsage(
                wall_seconds=elapsed,
                allocated_gpu_seconds=elapsed * len(request.gpu_ids),
                adapter=dict(adapter_response.resource_usage),
            ),
            "artifacts": artifacts,
            "error_code": adapter_response.error_code if adapter_failed else "",
            "error": adapter_response.error if adapter_failed else "",
        }
    )
    _write_record(record_path, completed)
    return completed


def derive_job_id(request_id: str) -> str:
    return derive_remote_job_id(request_id)


def _launch_worker(root: Path, job_id: str) -> int:
    job_dir = _job_dir(root, job_id)
    runner_stdout = (job_dir / "runner.log").open("ab")
    runner_stderr = (job_dir / "runner.err.log").open("ab")
    try:
        process = _spawn_worker_process(
            argv=[
                sys.executable,
                "-m",
                "app.execution.remote.runner",
                "--root",
                str(root),
                "_work",
                "--job-id",
                job_id,
            ],
            cwd=job_dir,
            stdout_handle=runner_stdout,
            stderr_handle=runner_stderr,
        )
    finally:
        runner_stdout.close()
        runner_stderr.close()
    return int(process.pid)


def _finish_failed(
    record_path: Path,
    record: RemoteJobRecord,
    request: RemoteJobRequest,
    monotonic_started: float,
    *,
    error_code: str,
    error: str,
    exit_code: int | None = None,
) -> RemoteJobRecord:
    elapsed = time.monotonic() - monotonic_started
    failed = record.model_copy(
        update={
            "state": RemoteJobState.FAILED,
            "heartbeat_at": _utcnow(),
            "finished_at": _utcnow(),
            "heartbeat_stale": False,
            "exit_code": exit_code,
            "resource_usage": _resource_usage(request, elapsed),
            "artifacts": _existing_log_artifacts(record_path.parent),
            "error_code": error_code,
            "error": error[-4000:],
        }
    )
    _write_record(record_path, failed)
    return failed


def _resource_usage(request: RemoteJobRequest, wall_seconds: float) -> RemoteResourceUsage:
    return RemoteResourceUsage(
        wall_seconds=max(0.0, wall_seconds),
        allocated_gpu_seconds=max(0.0, wall_seconds) * len(request.gpu_ids),
    )


def _read_adapter_response(job_dir: Path, request: RemoteJobRequest) -> AdapterResponse:
    response_path = _contained_path(job_dir, request.result_manifest_path)
    if not response_path.is_file():
        raise RemoteRunnerError(
            "result_manifest_missing",
            f"workload did not create {request.result_manifest_path}",
        )
    response = AdapterResponse.model_validate_json(response_path.read_text(encoding="utf-8"))
    expected_request_id = request.result_request_id or request.request_id
    if response.request_id != expected_request_id:
        raise RemoteRunnerError(
            "result_request_mismatch",
            "adapter response request_id does not match remote job request",
        )
    return response


def _collect_artifacts(
    job_dir: Path,
    request: RemoteJobRequest,
    response: AdapterResponse,
) -> tuple[RemoteOutputArtifact, ...]:
    artifacts: dict[str, Path] = {
        "result_manifest": _contained_path(job_dir, request.result_manifest_path),
    }
    for name, raw_path in response.artifacts.items():
        path = _optional_artifact_path(job_dir, raw_path)
        if path is not None:
            safe_name = _safe_artifact_name(name)
            if safe_name in artifacts:
                suffix = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
                safe_name = f"{safe_name[:118]}-{suffix}"
            artifacts[safe_name] = path
    for name, filename in (
        ("stdout_log", "stdout.log"),
        ("stderr_log", "stderr.log"),
    ):
        path = job_dir / filename
        if path.is_file():
            artifacts[name] = path
    return tuple(
        _output_artifact(name, path, root=job_dir)
        for name, path in sorted(artifacts.items())
    )


def _existing_log_artifacts(job_dir: Path) -> tuple[RemoteOutputArtifact, ...]:
    values: list[RemoteOutputArtifact] = []
    for name, filename in (
        ("stdout_log", "stdout.log"),
        ("stderr_log", "stderr.log"),
    ):
        path = job_dir / filename
        if path.is_file():
            values.append(_output_artifact(name, path, root=job_dir))
    return tuple(values)


def _output_artifact(name: str, path: Path, *, root: Path) -> RemoteOutputArtifact:
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(root.resolve(strict=True)).as_posix()
    except ValueError as exc:
        raise RemoteRunnerError(
            "artifact_path_escape",
            f"artifact '{name}' escapes the job directory",
        ) from exc
    if not resolved.is_file():
        raise RemoteRunnerError("artifact_missing", f"artifact '{name}' is not a file")
    return RemoteOutputArtifact(
        name=name,
        relative_path=relative,
        sha256=_sha256_file(resolved),
        size_bytes=resolved.stat().st_size,
    )


def _contained_path(root: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    combined = candidate if candidate.is_absolute() else root / candidate
    try:
        combined.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise RemoteRunnerError(
            "artifact_path_invalid",
            f"artifact path is missing or escapes job root: {raw_path}",
        ) from exc
    return combined


def _optional_artifact_path(root: Path, raw_path: str) -> Path | None:
    """Resolve file-valued adapter artifacts while preserving opaque refs."""

    candidate = Path(raw_path)
    combined = candidate if candidate.is_absolute() else root / candidate
    root_resolved = root.resolve(strict=True)
    resolved = combined.resolve(strict=False)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        if candidate.is_absolute() or ".." in candidate.parts:
            raise RemoteRunnerError(
                "artifact_path_escape",
                f"artifact path escapes job root: {raw_path}",
            ) from exc
        return None
    if not resolved.exists():
        return None
    if not resolved.is_file():
        raise RemoteRunnerError(
            "artifact_not_file",
            f"artifact path is not a file: {raw_path}",
        )
    return combined


def _safe_artifact_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    if not normalized:
        normalized = "artifact"
    normalized = normalized[:128]
    return validate_identifier(normalized, label="artifact name")


def _verify_input_artifacts(job_dir: Path, request: RemoteJobRequest) -> None:
    for artifact in request.input_artifacts:
        path = _contained_path(job_dir, artifact.relative_path)
        if path.stat().st_size != artifact.size_bytes:
            raise RemoteRunnerError(
                "input_size_mismatch",
                f"input artifact '{artifact.name}' has an unexpected size",
            )
        if _sha256_file(path) != artifact.sha256:
            raise RemoteRunnerError(
                "input_hash_mismatch",
                f"input artifact '{artifact.name}' failed SHA-256 verification",
            )


def _terminate_process_group(pid: int) -> None:
    try:
        _kill_process_group(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    time.sleep(0.2)
    try:
        _kill_process_group(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except ProcessLookupError:
        return


def _kill_process_group(pid: int, signal_number: int) -> None:
    if hasattr(os, "killpg"):
        os.killpg(pid, signal_number)
        return
    os.kill(pid, signal_number)


def _spawn_workload_process(
    *,
    argv: list[str],
    cwd: Path,
    environment: dict[str, str],
    stdout_handle: Any,
    stderr_handle: Any,
) -> subprocess.Popen[bytes]:
    if os.name == "nt":
        return subprocess.Popen(
            argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            close_fds=True,
            creationflags=int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)),
        )
    return subprocess.Popen(
        argv,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=stdout_handle,
        stderr=stderr_handle,
        close_fds=True,
        start_new_session=True,
    )


def _spawn_worker_process(
    *,
    argv: list[str],
    cwd: Path,
    stdout_handle: Any,
    stderr_handle: Any,
) -> subprocess.Popen[bytes]:
    if os.name == "nt":
        return subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            close_fds=True,
            creationflags=int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)),
        )
    return subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=stdout_handle,
        stderr=stderr_handle,
        close_fds=True,
        start_new_session=True,
    )


def _wait_for_start(path: Path) -> None:
    deadline = time.monotonic() + 30.0
    while not path.is_file():
        if time.monotonic() >= deadline:
            raise RemoteRunnerError("worker_start_timeout", "worker launch gate timed out")
        time.sleep(0.05)


def _job_dir(root: Path, job_id: str) -> Path:
    normalized_id = validate_identifier(job_id, label="job_id")
    return _normalized_root(root) / "jobs" / normalized_id


def _normalized_root(root: Path) -> Path:
    expanded = root.expanduser()
    if not expanded.is_absolute():
        raise RemoteRunnerError("invalid_remote_root", "remote root must be absolute")
    return expanded.resolve()


def _read_record(path: Path) -> RemoteJobRecord:
    if not path.is_file():
        raise RemoteRunnerError("job_not_found", f"remote job record not found: {path.parent.name}")
    return RemoteJobRecord.model_validate_json(path.read_text(encoding="utf-8"))


def _write_record(path: Path, record: RemoteJobRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _emit_json(value: object) -> None:
    if isinstance(value, BaseModel):
        rendered = value.model_dump_json()
    else:
        rendered = json.dumps(value, separators=(",", ":"), sort_keys=True)
    sys.stdout.write(f"{rendered}\n")
    sys.stdout.flush()


def _die(error: RemoteRunnerError) -> NoReturn:
    sys.stderr.write(
        json.dumps(
            {"error_code": error.code, "error": str(error)},
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
    raise SystemExit(2)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mars_remote_runner")
    parser.add_argument("--root", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("readiness")
    for action in ("stage", "status", "cancel", "fetch", "_work"):
        command = subparsers.add_parser(action)
        command.add_argument("--job-id", required=True)
    submit = subparsers.add_parser("submit")
    submit.add_argument("--job-id", required=True)
    submit.add_argument("--request-sha256", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root: Path = args.root
    action: str = args.action
    try:
        if action == "readiness":
            _emit_json(readiness(root))
        elif action == "stage":
            stage_dir = stage_job(root, str(args.job_id))
            _emit_json({"protocol": "remote_stage.v1", "status": "ready", "path": str(stage_dir)})
        elif action == "submit":
            _emit_json(submit_job(root, str(args.job_id), str(args.request_sha256)))
        elif action == "status":
            _emit_json(status_job(root, str(args.job_id)))
        elif action == "cancel":
            _emit_json(cancel_job(root, str(args.job_id)))
        elif action == "fetch":
            _emit_json(fetch_job(root, str(args.job_id)))
        elif action == "_work":
            _emit_json(work_job(root, str(args.job_id)))
        else:
            raise RemoteRunnerError("unsupported_action", f"unsupported action: {action}")
    except RemoteRunnerError as exc:
        _die(exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
