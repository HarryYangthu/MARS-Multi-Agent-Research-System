"""Durable, idempotent submission of trusted local commands.

The detached worker owns the command process group. Clients never signal a
persisted PID, and an unknown outcome never causes an automatic re-submission.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Literal

from filelock import FileLock, Timeout as FileLockTimeout
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.harness.persistence import atomic_write_json, path_lock
from app.harness.tools.process_runtime import sanitized_subprocess_environment
from app.harness.tools.execution.local_command import _file_hash, _read_result

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_CHILDREN: list[subprocess.Popen[bytes]] = []


class LocalJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    run_id: str
    attempt_id: str
    job_id: str
    experiment_id: str
    project: str = Field(min_length=1)
    argv: tuple[str, ...] = Field(min_length=1)
    cwd: str
    timeout_seconds: float = Field(gt=0, strict=True)
    max_output_bytes: int = Field(gt=0, strict=True)
    required_metrics: tuple[str, ...] = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None
    steps: int = Field(gt=0, strict=True)

    @field_validator("run_id", "attempt_id", "job_id", "experiment_id")
    @classmethod
    def identity(cls, value: str) -> str:
        if _ID.fullmatch(value) is None:
            raise ValueError("invalid local job identity")
        return value

    @field_validator("argv", "required_metrics")
    @classmethod
    def strings(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or "\x00" in value for value in values):
            raise ValueError("nonempty strings without NUL are required")
        return values

    @field_validator("cwd")
    @classmethod
    def absolute_directory(cls, value: str) -> str:
        if not Path(value).is_absolute() or ".." in Path(value).parts or "\x00" in value:
            raise ValueError("cwd must be an absolute directory without traversal")
        return value


class LocalJobStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    attempt_id: str
    job_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled", "unknown"]
    owner_active: bool
    stop_requested: bool
    deadline_at: float
    receipt: dict[str, Any] | None = None


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                               ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _plain_directory(path: Path) -> Path:
    """Allow OS ancestor aliases (e.g. /tmp), never a linked selected root."""
    if path.is_symlink() or not path.is_dir():
        raise ValueError("local runner directory must be an existing plain directory")
    return path.resolve()


def _child_directory(parent: Path, name: str, *, create: bool) -> Path:
    target = parent / name
    if target.is_symlink():
        raise ValueError("local runner directory must not be a symbolic link")
    if create:
        target.mkdir(exist_ok=True)
    if not target.is_dir() or target.resolve().parent != parent:
        raise ValueError("local runner directory is missing or out of scope")
    return target


def _check_files(directory: Path) -> None:
    for name in ("submission.json", "job.json", "state.json", "stop.json", "worker.lock", "control.lock",
                 "execution_receipt.json", "stdout.log", "stderr.log", "result.json"):
        path = directory / name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("local runner control/output path is not a plain file")


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("local runner record is missing or invalid")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("local runner record must be an object")
    return value


def _submission(directory: Path) -> tuple[LocalJobSpec, dict[str, Any]]:
    _check_files(directory)
    record = _read_json(directory / "submission.json")
    spec = LocalJobSpec.model_validate(record.get("spec"))
    if (record.get("schema") != "local_job_submission.v1" or record.get("spec_sha256") != _digest(spec.model_dump(mode="json"))
            or record.get("run_root") != str(directory.parents[2]) or spec.job_id != directory.name
            or spec.run_id != directory.parents[2].name or type(record.get("deadline_at")) not in (int, float)
            or not math.isfinite(record["deadline_at"]) or type(record.get("submitted_at")) not in (int, float)
            or not math.isfinite(record["submitted_at"])
            or record["deadline_at"] != record["submitted_at"] + spec.timeout_seconds):
        raise ValueError("local runner submission identity or fingerprint mismatch")
    return spec, record


def _owner_active(directory: Path) -> bool:
    lease = FileLock(directory / "worker.lock", timeout=0)
    try:
        lease.acquire()
    except FileLockTimeout:
        return True
    lease.release()
    return False


class LocalRunner:
    def __init__(self, run_root: Path) -> None:
        if os.name != "posix":
            raise ValueError("independent local runner currently supports POSIX hosts only")
        self.root = _plain_directory(run_root)
        self.run_id = self.root.name

    def _directory(self, job_id: str, *, create: bool = False) -> Path:
        if _ID.fullmatch(job_id) is None:
            raise ValueError("invalid local job identity")
        execution = _child_directory(self.root, "execution", create=create)
        jobs = _child_directory(execution, "local_jobs", create=create)
        directory = _child_directory(jobs, job_id, create=create)
        _check_files(directory)
        return directory

    def submit(self, spec: LocalJobSpec) -> LocalJobStatus:
        if spec.run_id != self.run_id:
            raise ValueError("local job belongs to another run")
        _plain_directory(Path(spec.cwd))
        executable = Path(spec.argv[0])
        if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("local command requires an explicit executable")
        directory = self._directory(spec.job_id, create=True)
        with path_lock(directory / "control.lock"):
            if (directory / "submission.json").exists():
                previous, _ = _submission(directory)
                if previous != spec:
                    raise ValueError("job identity is already bound to a different submission")
            else:
                submitted_at = time.time()
                payload = spec.model_dump(mode="json")
                request: dict[str, Any] = {
                    "schema": "local_command_request.v1", "invocation_id": spec.job_id,
                    "run_id": spec.run_id, "experiment_id": spec.experiment_id, "project": spec.project,
                    "config": spec.config, "seed": spec.seed, "steps": spec.steps, "output_dir": str(directory),
                }
                atomic_write_json(directory / "job.json", request)
                atomic_write_json(directory / "submission.json", {
                    "schema": "local_job_submission.v1", "spec": payload, "spec_sha256": _digest(payload),
                    "run_root": str(self.root), "submitted_at": submitted_at,
                    "deadline_at": submitted_at + spec.timeout_seconds, "request_sha256": _digest(request),
                })
                atomic_write_json(directory / "state.json", {"status": "queued"})
                # Persisting submission precedes spawn. A crash here is an unknown
                # submission, never permission to run the same command twice.
                worker = Path(__file__).with_name("worker.py")
                child = subprocess.Popen([sys.executable, "-I", str(worker), str(directory)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=sanitized_subprocess_environment(), start_new_session=True, close_fds=True)
                _CHILDREN[:] = [item for item in _CHILDREN if item.poll() is None]
                _CHILDREN.append(child)
        return self.status(spec.job_id)

    def status(self, job_id: str) -> LocalJobStatus:
        directory = self._directory(job_id)
        with path_lock(directory / "control.lock"):
            spec, record = _submission(directory)
            state = _read_json(directory / "state.json")
            active = _owner_active(directory)
            receipt = _read_json(directory / "execution_receipt.json") if (directory / "execution_receipt.json").exists() else None
            status = state.get("status")
            if receipt is not None:
                if (receipt.get("schema") != "local_command_receipt.v1" or receipt.get("run_id") != spec.run_id
                        or receipt.get("invocation_id") != spec.job_id or receipt.get("attempt_id") != spec.attempt_id
                        or receipt.get("experiment_id") != spec.experiment_id
                        or receipt.get("submission_sha256") != record["spec_sha256"]
                        or receipt.get("project") != spec.project or receipt.get("deadline_at") != record["deadline_at"]
                        or receipt.get("status") not in _TERMINAL):
                    raise ValueError("local runner receipt identity mismatch")
                if receipt["status"] == "completed":
                    request = _read_json(directory / "job.json")
                    if (type(receipt.get("returncode")) is not int or receipt["returncode"] != 0
                            or receipt.get("error") != "" or _digest(request) != record["request_sha256"]
                            or receipt.get("request_sha256") != _file_hash(directory / "job.json")
                            or receipt.get("result_sha256") != _file_hash(directory / "result.json")):
                        raise ValueError("local runner completed receipt is inconsistent")
                    metrics, curve, evidence = _read_result(directory / "result.json", request, spec.required_metrics)
                    if (receipt.get("metrics") != metrics or receipt.get("loss_curve") != curve
                            or receipt.get("evidence") != evidence):
                        raise ValueError("local runner measurement receipt is inconsistent")
                status = receipt["status"]
            elif status not in {"queued", "running"}:
                raise ValueError("local runner state has no matching receipt")
            elif not active and (status == "running" or time.time() >= record["deadline_at"]):
                status = "unknown"
            _CHILDREN[:] = [item for item in _CHILDREN if item.poll() is None]
            return LocalJobStatus.model_validate({"run_id": spec.run_id, "attempt_id": spec.attempt_id, "job_id": spec.job_id,
                "status": status, "owner_active": active, "stop_requested": (directory / "stop.json").exists(),
                "deadline_at": record["deadline_at"], "receipt": receipt})

    def stop(self, job_id: str) -> LocalJobStatus:
        directory = self._directory(job_id)
        with path_lock(directory / "control.lock"):
            spec, _ = _submission(directory)
            if not (directory / "execution_receipt.json").exists() and not (directory / "stop.json").exists():
                atomic_write_json(directory / "stop.json", {"run_id": spec.run_id, "job_id": spec.job_id,
                    "attempt_id": spec.attempt_id, "requested_at": time.time()})
        return self.status(job_id)
