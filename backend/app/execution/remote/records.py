"""Typed records for the durable ``remote_job.v1`` protocol."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
import hashlib
from pathlib import Path, PurePosixPath
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.execution.adapters.base import AdapterResponse

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WORKLOAD_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./:=,+@%-]{1,1024}$")
_SHELL_EXECUTABLES = {"bash", "dash", "fish", "sh", "zsh"}
_EVAL_FLAGS = {"-c", "-e", "--eval"}


def validate_identifier(value: str, *, label: str) -> str:
    """Reject identifiers that cannot safely become one path component."""

    normalized = value.strip()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(
            f"{label} must match {_IDENTIFIER_RE.pattern!r} and contain no path separators"
        )
    return normalized


def validate_relative_path(value: str, *, label: str) -> str:
    """Return a normalized, traversal-free POSIX relative path."""

    normalized = value.strip()
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or normalized != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"{label} must be a normalized relative POSIX path")
    return normalized


class RemoteJobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {
            RemoteJobState.SUCCEEDED,
            RemoteJobState.FAILED,
            RemoteJobState.CANCELLED,
        }


class RemoteInputArtifact(BaseModel):
    """Input metadata persisted remotely; local paths are never serialized."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    relative_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        return validate_identifier(value, label="input artifact name")

    @field_validator("relative_path")
    @classmethod
    def _valid_relative_path(cls, value: str) -> str:
        return validate_relative_path(value, label="input artifact path")


class RemoteOutputArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    relative_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        return validate_identifier(value, label="output artifact name")

    @field_validator("relative_path")
    @classmethod
    def _valid_relative_path(cls, value: str) -> str:
        return validate_relative_path(value, label="output artifact path")


class RemoteJobRequest(BaseModel):
    """Trusted workload manifest uploaded before invoking the fixed runner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: Literal["remote_job.v1"] = "remote_job.v1"
    request_id: str
    project: str
    run_id: str
    candidate_id: str
    workload_argv: tuple[str, ...] = Field(min_length=1)
    input_artifacts: tuple[RemoteInputArtifact, ...] = ()
    result_manifest_path: str = "response.json"
    result_request_id: str = Field(default="", max_length=4096)
    timeout_seconds: float = Field(default=14_400.0, gt=0.0, le=604_800.0)
    heartbeat_interval_seconds: float = Field(default=30.0, ge=0.1, le=300.0)
    gpu_ids: tuple[str, ...] = ()

    @field_validator("request_id", "project", "run_id", "candidate_id")
    @classmethod
    def _valid_identifier(cls, value: str, info: Any) -> str:
        return validate_identifier(value, label=str(info.field_name))

    @field_validator("workload_argv")
    @classmethod
    def _valid_workload_argv(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(_WORKLOAD_TOKEN_RE.fullmatch(item) is None for item in value):
            raise ValueError(
                "workload argv must contain only bounded control tokens; "
                "candidate content belongs in uploaded artifacts"
            )
        executable = PurePosixPath(value[0]).name.lower()
        if executable in _SHELL_EXECUTABLES:
            raise ValueError("shell interpreters are not valid remote workload entrypoints")
        if executable.startswith(("python", "node", "ruby", "perl")) and any(
            item in _EVAL_FLAGS for item in value[1:]
        ):
            raise ValueError("inline code evaluation is forbidden for remote workloads")
        return value

    @field_validator("result_manifest_path")
    @classmethod
    def _valid_result_manifest(cls, value: str) -> str:
        return validate_relative_path(value, label="result manifest path")

    @field_validator("result_request_id")
    @classmethod
    def _valid_result_request_id(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("result_request_id must not contain NUL")
        return value

    @field_validator("gpu_ids")
    @classmethod
    def _valid_gpu_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.isdigit() for item in value):
            raise ValueError("gpu_ids must contain decimal device identifiers")
        if len(set(value)) != len(value):
            raise ValueError("gpu_ids must be unique")
        return value


class RemoteResourceUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    wall_seconds: float = Field(default=0.0, ge=0.0)
    allocated_gpu_seconds: float = Field(default=0.0, ge=0.0)
    adapter: dict[str, float] = Field(default_factory=dict)


class RemoteJobRecord(BaseModel):
    """Durable status returned by submit, status, cancel, and fetch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: Literal["remote_job.v1"] = "remote_job.v1"
    request_id: str
    job_id: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: RemoteJobState
    submitted_at: datetime
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    finished_at: datetime | None = None
    worker_pid: int | None = Field(default=None, gt=0)
    workload_pid: int | None = Field(default=None, gt=0)
    heartbeat_stale: bool = False
    exit_code: int | None = None
    adapter_response: AdapterResponse | None = None
    resource_usage: RemoteResourceUsage = Field(default_factory=RemoteResourceUsage)
    artifacts: tuple[RemoteOutputArtifact, ...] = ()
    error_code: str = ""
    error: str = ""

    @field_validator("request_id", "job_id")
    @classmethod
    def _valid_identifier(cls, value: str, info: Any) -> str:
        return validate_identifier(value, label=str(info.field_name))


class RemoteReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: Literal["remote_readiness.v1"] = "remote_readiness.v1"
    status: Literal["ready", "blocked"]
    runner_version: str = ""
    findings: tuple[str, ...] = ()
    error_code: str = ""
    error: str = ""


class DownloadedArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    local_path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class RemoteFetchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record: RemoteJobRecord
    downloaded: tuple[DownloadedArtifact, ...] = ()


def is_sha256(value: str) -> bool:
    return _SHA256_RE.fullmatch(value) is not None


def derive_remote_job_id(request_id: str) -> str:
    normalized = validate_identifier(request_id, label="request_id")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"rj-{digest}"
