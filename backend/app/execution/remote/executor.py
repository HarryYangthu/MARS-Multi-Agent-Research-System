"""Recoverable SSH client for the durable remote GPU runner."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any

import yaml

from app.execution.remote.records import (
    DownloadedArtifact,
    RemoteFetchResult,
    RemoteInputArtifact,
    RemoteJobRecord,
    RemoteJobRequest,
    RemoteJobState,
    RemoteReadiness,
    derive_remote_job_id as derive_remote_job_id,
    validate_identifier,
    validate_relative_path,
)
from app.execution.remote.transport import (
    RemoteTransport,
    SystemSshTransport,
    TransportResult,
)
from app.settings import env_or_local, repo_root

_SAFE_REMOTE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./:-]+$")
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]{0,63}$")
_RUNNER_MODULE = "app.execution.remote.runner"


class RemoteExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RemoteInputUpload:
    name: str
    local_path: Path
    relative_path: str

    def __post_init__(self) -> None:
        validate_identifier(self.name, label="input upload name")
        validate_relative_path(self.relative_path, label="input upload path")
        if len(PurePosixPath(self.relative_path).parts) != 2 or not self.relative_path.startswith(
            "inputs/"
        ):
            raise ValueError("input upload paths must be one file directly below inputs/")


@dataclass(frozen=True)
class RemoteExecutorConfig:
    enabled: bool = False
    host: str = ""
    port: int = 22
    user: str = ""
    key_path: Path | None = None
    known_hosts_path: Path | None = None
    remote_root: str = ""
    python: str = "python3"
    gpu_ids: tuple[str, ...] = ()
    connect_timeout_seconds: float = 10.0
    command_timeout_seconds: float = 60.0
    transfer_timeout_seconds: float = 900.0
    heartbeat_stale_seconds: float = 600.0

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65_535:
            raise ValueError("SSH port must be between 1 and 65535")
        if self.host and _HOST_RE.fullmatch(self.host) is None:
            raise ValueError("remote host contains unsupported characters")
        if self.user and _USER_RE.fullmatch(self.user) is None:
            raise ValueError("remote user contains unsupported characters")
        if self.python and _SAFE_REMOTE_TOKEN_RE.fullmatch(self.python) is None:
            raise ValueError("remote python contains unsupported characters")
        if self.remote_root:
            root = PurePosixPath(self.remote_root)
            if (
                not root.is_absolute()
                or ".." in root.parts
                or root.as_posix() != self.remote_root
                or _SAFE_REMOTE_TOKEN_RE.fullmatch(self.remote_root) is None
            ):
                raise ValueError("remote_root must be a normalized absolute POSIX path")
        if any(not value.isdigit() for value in self.gpu_ids):
            raise ValueError("gpu_ids must be decimal device identifiers")
        if len(set(self.gpu_ids)) != len(self.gpu_ids):
            raise ValueError("gpu_ids must be unique")
        for timeout_label, timeout_value in (
            ("connect timeout", self.connect_timeout_seconds),
            ("command timeout", self.command_timeout_seconds),
            ("transfer timeout", self.transfer_timeout_seconds),
            ("heartbeat stale threshold", self.heartbeat_stale_seconds),
        ):
            if timeout_value <= 0:
                raise ValueError(f"{timeout_label} must be positive")

    def missing_fields(self) -> tuple[str, ...]:
        missing: list[str] = []
        for label, value in (
            ("MARS_REMOTE_SSH_HOST", self.host),
            ("MARS_REMOTE_SSH_USER", self.user),
            ("MARS_REMOTE_SSH_KEY_PATH", self.key_path),
            ("MARS_REMOTE_SSH_KNOWN_HOSTS", self.known_hosts_path),
            ("MARS_REMOTE_ROOT", self.remote_root),
            ("MARS_REMOTE_PYTHON", self.python),
        ):
            if not value:
                missing.append(label)
        return tuple(missing)


class RemoteExecutor:
    """Submit and recover jobs through a fixed remote runner entrypoint."""

    def __init__(
        self,
        config: RemoteExecutorConfig,
        *,
        transport: RemoteTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        if transport is not None:
            self._transport: RemoteTransport | None = transport
        elif config.key_path is not None and config.known_hosts_path is not None:
            self._transport = SystemSshTransport(
                host=config.host,
                port=config.port,
                user=config.user,
                key_path=config.key_path,
                known_hosts_path=config.known_hosts_path,
                connect_timeout_seconds=config.connect_timeout_seconds,
            )
        else:
            self._transport = None

    async def readiness(self) -> RemoteReadiness:
        if not self._config.enabled:
            return RemoteReadiness(
                status="blocked",
                error_code="remote_executor_disabled",
                error="remote GPU executor is disabled",
            )
        missing = self._config.missing_fields()
        if missing:
            return RemoteReadiness(
                status="blocked",
                findings=missing,
                error_code="remote_configuration_missing",
                error="remote GPU executor configuration is incomplete",
            )
        transport = self._require_transport()
        local_findings = transport.local_findings()
        if local_findings:
            return RemoteReadiness(
                status="blocked",
                findings=local_findings,
                error_code="local_ssh_prerequisite_missing",
                error="local OpenSSH prerequisites are unavailable",
            )
        result = await transport.run(
            self._runner_argv("readiness"),
            timeout_seconds=self._config.command_timeout_seconds,
        )
        if result.returncode != 0:
            return RemoteReadiness(
                status="blocked",
                findings=_transport_findings(result),
                error_code="remote_runner_unavailable",
                error="remote runner readiness probe failed",
            )
        try:
            return RemoteReadiness.model_validate_json(result.stdout)
        except ValueError as exc:
            return RemoteReadiness(
                status="blocked",
                error_code="remote_runner_invalid_response",
                error=str(exc),
            )

    async def submit(
        self,
        request: RemoteJobRequest,
        *,
        uploads: tuple[RemoteInputUpload, ...] = (),
    ) -> RemoteJobRecord:
        self._ensure_configured()
        transport = self._require_transport()
        job_id = derive_remote_job_id(request.request_id)
        artifacts = tuple(_input_artifact(upload) for upload in uploads)
        effective_request = request.model_copy(
            update={
                "gpu_ids": self._config.gpu_ids,
                "input_artifacts": artifacts,
            }
        )
        payload = effective_request.model_dump_json(indent=2).encode("utf-8")
        request_sha256 = hashlib.sha256(payload).hexdigest()

        await self._run_control("stage", "--job-id", job_id)
        with tempfile.TemporaryDirectory(prefix="mars-remote-request-") as temp_dir:
            request_path = Path(temp_dir) / "request.json"
            request_path.write_bytes(payload)
            await self._upload_checked(
                request_path,
                self._remote_path("incoming", job_id, "request.json"),
            )
            for upload in uploads:
                await self._upload_checked(
                    upload.local_path,
                    self._remote_path("incoming", job_id, upload.relative_path),
                )

        result = await transport.run(
            self._runner_argv(
                "submit",
                "--job-id",
                job_id,
                "--request-sha256",
                request_sha256,
            ),
            timeout_seconds=self._config.command_timeout_seconds,
        )
        return self._parse_record(result, operation="submit")

    async def status(self, job_id: str) -> RemoteJobRecord:
        validate_identifier(job_id, label="job_id")
        result = await self._run_control("status", "--job-id", job_id)
        return self._with_stale_status(self._parse_record(result, operation="status"))

    async def status_for_request(self, request_id: str) -> RemoteJobRecord:
        return await self.status(derive_remote_job_id(request_id))

    async def cancel(self, job_id: str) -> RemoteJobRecord:
        validate_identifier(job_id, label="job_id")
        result = await self._run_control("cancel", "--job-id", job_id)
        return self._parse_record(result, operation="cancel")

    async def fetch(self, job_id: str, destination: Path) -> RemoteFetchResult:
        validate_identifier(job_id, label="job_id")
        result = await self._run_control("fetch", "--job-id", job_id)
        record = self._with_stale_status(self._parse_record(result, operation="fetch"))
        destination.mkdir(parents=True, exist_ok=True)
        downloaded: list[DownloadedArtifact] = []
        transport = self._require_transport()
        for artifact in record.artifacts:
            local_path = destination / PurePosixPath(artifact.relative_path)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            partial_path = local_path.with_name(f"{local_path.name}.part")
            transfer = await transport.download(
                self._remote_path("jobs", job_id, artifact.relative_path),
                partial_path,
                timeout_seconds=self._config.transfer_timeout_seconds,
            )
            _raise_for_transport(transfer, operation=f"download {artifact.name}")
            actual_sha256 = _sha256_file(partial_path)
            if actual_sha256 != artifact.sha256:
                raise RemoteExecutionError(
                    "artifact_hash_mismatch",
                    f"downloaded artifact '{artifact.name}' failed SHA-256 verification",
                )
            os.replace(partial_path, local_path)
            downloaded.append(
                DownloadedArtifact(
                    name=artifact.name,
                    local_path=local_path,
                    sha256=actual_sha256,
                    size_bytes=local_path.stat().st_size,
                )
            )
        return RemoteFetchResult(record=record, downloaded=tuple(downloaded))

    async def _upload_checked(self, local_path: Path, remote_path: str) -> None:
        if not local_path.is_file():
            raise RemoteExecutionError(
                "input_artifact_missing",
                f"input artifact does not exist: {local_path}",
            )
        result = await self._require_transport().upload(
            local_path,
            remote_path,
            timeout_seconds=self._config.transfer_timeout_seconds,
        )
        _raise_for_transport(result, operation=f"upload {local_path.name}")

    async def _run_control(self, action: str, *args: str) -> TransportResult:
        self._ensure_configured()
        result = await self._require_transport().run(
            self._runner_argv(action, *args),
            timeout_seconds=self._config.command_timeout_seconds,
        )
        _raise_for_transport(result, operation=action)
        return result

    def _runner_argv(self, action: str, *args: str) -> tuple[str, ...]:
        if not _SAFE_REMOTE_TOKEN_RE.fullmatch(action):
            raise ValueError("invalid remote runner action")
        if any(_SAFE_REMOTE_TOKEN_RE.fullmatch(item) is None for item in args):
            raise ValueError("remote runner arguments must be safe control tokens")
        return (
            self._config.python,
            "-m",
            _RUNNER_MODULE,
            "--root",
            self._config.remote_root,
            action,
            *args,
        )

    def _remote_path(self, *parts: str) -> str:
        root = PurePosixPath(self._config.remote_root)
        path = root.joinpath(*(PurePosixPath(part) for part in parts))
        if root not in path.parents:
            raise ValueError("remote path escaped configured root")
        value = path.as_posix()
        if _SAFE_REMOTE_TOKEN_RE.fullmatch(value) is None:
            raise ValueError("remote path contains unsupported characters")
        return value

    def _parse_record(
        self,
        result: TransportResult,
        *,
        operation: str,
    ) -> RemoteJobRecord:
        _raise_for_transport(result, operation=operation)
        try:
            return RemoteJobRecord.model_validate_json(result.stdout)
        except ValueError as exc:
            raise RemoteExecutionError(
                "remote_runner_invalid_response",
                f"{operation} returned an invalid remote_job.v1 record: {exc}",
            ) from exc

    def _with_stale_status(self, record: RemoteJobRecord) -> RemoteJobRecord:
        heartbeat = record.heartbeat_at or record.started_at or record.submitted_at
        now = self._clock()
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        stale = (
            record.state in {RemoteJobState.QUEUED, RemoteJobState.RUNNING}
            and (now - heartbeat).total_seconds()
            > self._config.heartbeat_stale_seconds
        )
        return record.model_copy(update={"heartbeat_stale": stale})

    def _ensure_configured(self) -> None:
        if not self._config.enabled:
            raise RemoteExecutionError(
                "remote_executor_disabled",
                "remote GPU executor is disabled",
            )
        missing = self._config.missing_fields()
        if missing:
            raise RemoteExecutionError(
                "remote_configuration_missing",
                f"missing remote configuration: {', '.join(missing)}",
            )

    def _require_transport(self) -> RemoteTransport:
        if self._transport is None:
            raise RemoteExecutionError(
                "remote_transport_unconfigured",
                "remote transport cannot be constructed from the current configuration",
            )
        return self._transport


def load_remote_executor_config(
    path: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> RemoteExecutorConfig:
    """Resolve non-secret YAML policy and environment-backed connection data."""

    config_path = path or (repo_root() / "configs" / "execution.yaml")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    execution = _mapping(raw).get("execution", {})
    remote = _mapping(_mapping(execution).get("remote_gpu", {}))
    env_names = _mapping(remote.get("env", {}))
    def env_value(key: str) -> str:
        name = str(env_names.get(key, "")).strip()
        if not name:
            return ""
        if environ is not None:
            return str(environ.get(name, "")).strip()
        return env_or_local(name).strip()

    enabled_default = bool(remote.get("enabled", False))
    enabled = _parse_bool(env_value("enabled"), default=enabled_default)
    key_path = env_value("key_path")
    known_hosts = env_value("known_hosts")
    gpu_ids = tuple(
        item.strip()
        for item in env_value("gpu_ids").split(",")
        if item.strip()
    )
    return RemoteExecutorConfig(
        enabled=enabled,
        host=env_value("host"),
        port=_parse_int(env_value("port"), default=22),
        user=env_value("user"),
        key_path=Path(key_path).expanduser() if key_path else None,
        known_hosts_path=Path(known_hosts).expanduser() if known_hosts else None,
        remote_root=env_value("remote_root"),
        python=env_value("python") or "python3",
        gpu_ids=gpu_ids,
        connect_timeout_seconds=_parse_float(
            remote.get("connect_timeout_seconds"), default=10.0
        ),
        command_timeout_seconds=_parse_float(
            remote.get("command_timeout_seconds"), default=60.0
        ),
        transfer_timeout_seconds=_parse_float(
            remote.get("transfer_timeout_seconds"), default=900.0
        ),
        heartbeat_stale_seconds=_parse_float(
            remote.get("heartbeat_stale_seconds"), default=600.0
        ),
    )


def _input_artifact(upload: RemoteInputUpload) -> RemoteInputArtifact:
    if not upload.local_path.is_file():
        raise RemoteExecutionError(
            "input_artifact_missing",
            f"input artifact does not exist: {upload.local_path}",
        )
    return RemoteInputArtifact(
        name=upload.name,
        relative_path=upload.relative_path,
        sha256=_sha256_file(upload.local_path),
        size_bytes=upload.local_path.stat().st_size,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _raise_for_transport(result: TransportResult, *, operation: str) -> None:
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout or "remote transport failed")[-4000:]
    raise RemoteExecutionError(
        "remote_transport_failed",
        f"{operation} failed with exit code {result.returncode}: {detail}",
    )


def _transport_findings(result: TransportResult) -> tuple[str, ...]:
    detail = (result.stderr or result.stdout).strip()
    return (detail[-4000:],) if detail else ()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _parse_bool(value: str, *, default: bool) -> bool:
    if not value:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _parse_int(value: object, *, default: int) -> int:
    if value in {None, ""}:
        return default
    return int(str(value))


def _parse_float(value: object, *, default: float) -> float:
    if value in {None, ""}:
        return default
    return float(str(value))
