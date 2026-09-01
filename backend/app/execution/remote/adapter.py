"""ProjectAdapter bridge for the durable remote GPU executor."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time
from typing import Protocol

from app.execution.adapters.base import (
    AdapterAction,
    AdapterRequest,
    AdapterResponse,
)
from app.execution.adapters.workspace import (
    WORKSPACE_ARCHIVE_REMOTE_PATH,
    WORKSPACE_ARCHIVE_UPLOAD_NAME,
    WORKSPACE_CONFIG_KEY,
    WORKSPACE_RECEIPT_REMOTE_PATH,
    WORKSPACE_RECEIPT_UPLOAD_NAME,
    WorkspaceResolver,
    bind_workspace_request,
    workspace_binding_for_receipt,
)
from app.execution.remote.executor import RemoteInputUpload
from app.execution.remote.records import (
    RemoteFetchResult,
    RemoteJobRecord,
    RemoteJobRequest,
    RemoteJobState,
    RemoteReadiness,
    validate_identifier,
)
from app.harness.discovery.code_workspace_transfer import (
    CodeWorkspaceTransferError,
    validate_code_workspace_transfer_package,
)

_WORKER_MODULE = "app.execution.remote.adapter_worker"
_REMOTE_REQUEST_PATH = "inputs/adapter_request.json"
_REMOTE_RESPONSE_PATH = "response.json"


class RemoteJobClient(Protocol):
    """Narrow seam implemented by RemoteExecutor and fake clients."""

    async def readiness(self) -> RemoteReadiness: ...

    async def submit(
        self,
        request: RemoteJobRequest,
        *,
        uploads: tuple[RemoteInputUpload, ...] = (),
    ) -> RemoteJobRecord: ...

    async def status(self, job_id: str) -> RemoteJobRecord: ...

    async def fetch(self, job_id: str, destination: Path) -> RemoteFetchResult: ...


class _RemoteAdapterFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, record: RemoteJobRecord | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.record = record


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


@dataclass(frozen=True)
class RemoteProjectAdapter:
    """Run an adapter.v1 executable through RemoteExecutor.

    ``trusted_adapter_argv`` is fixed at composition time. AdapterRequest data,
    including LLM-authored config, is uploaded as JSON and delivered only on
    stdin by the remote worker; it can never alter the executed argv.
    """

    name: str
    client: RemoteJobClient
    trusted_adapter_argv: tuple[str, ...]
    artifact_root: Path
    workspace_resolver: WorkspaceResolver | None = None
    remote_worker_python: str = "python3"
    adapter_timeout_seconds: float = 900.0
    max_wait_seconds: float = 960.0
    poll_interval_seconds: float = 2.0
    max_status_errors: int = 3
    heartbeat_interval_seconds: float = 30.0
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = _default_sleep

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("remote adapter name must not be empty")
        if not self.trusted_adapter_argv:
            raise ValueError("trusted adapter argv must not be empty")
        if self.adapter_timeout_seconds <= 0:
            raise ValueError("adapter timeout must be positive")
        if self.max_wait_seconds <= 0:
            raise ValueError("max wait must be positive")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll interval must be positive")
        if self.max_status_errors < 0:
            raise ValueError("max status errors must be non-negative")
        if not 0.1 <= self.heartbeat_interval_seconds <= 300.0:
            raise ValueError("heartbeat interval must be between 0.1 and 300 seconds")
        # Reuse the remote protocol validator to reject shell/eval commands and
        # any argv token that could carry inline candidate source. Validate the
        # inner trusted adapter independently; otherwise a shell token after
        # the worker's ``--`` separator would escape this check.
        RemoteJobRequest(
            request_id="adapter-validation",
            project="validation",
            run_id="validation",
            candidate_id="validation",
            workload_argv=self._workload_argv(),
        )
        RemoteJobRequest(
            request_id="trusted-adapter-validation",
            project="validation",
            run_id="validation",
            candidate_id="validation",
            workload_argv=self.trusted_adapter_argv,
        )

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        if request.action == AdapterAction.READINESS:
            try:
                readiness = await self.client.readiness()
            except Exception as exc:
                return _failed_response(
                    request,
                    code="remote_readiness_failed",
                    message=str(exc),
                    status="blocked",
                )
            if readiness.status != "ready":
                return AdapterResponse(
                    request_id=request.request_id,
                    status="blocked",
                    findings=readiness.findings,
                    error_code=readiness.error_code or "remote_not_ready",
                    error=readiness.error or "remote executor is not ready",
                )

        if WORKSPACE_CONFIG_KEY in request.config:
            return _failed_response(
                request,
                code="remote_workspace_binding_untrusted",
                message="workspace binding must be supplied by the trusted resolver",
            )
        worker_request = request.model_copy(update={"output_dir": "artifacts"})
        workspace_uploads: tuple[RemoteInputUpload, ...] = ()
        if self.workspace_resolver is not None:
            try:
                package = await self.workspace_resolver.resolve(request)
                if package is not None:
                    validate_code_workspace_transfer_package(package)
                    binding = workspace_binding_for_receipt(
                        package.receipt,
                        receipt_sha256=package.receipt_sha256,
                    )
                    if binding.candidate_id != request.candidate_id:
                        raise CodeWorkspaceTransferError(
                            "workspace candidate_id does not match AdapterRequest"
                        )
                    worker_request = bind_workspace_request(worker_request, binding)
                    workspace_uploads = (
                        RemoteInputUpload(
                            name=WORKSPACE_ARCHIVE_UPLOAD_NAME,
                            local_path=package.archive_path,
                            relative_path=WORKSPACE_ARCHIVE_REMOTE_PATH,
                        ),
                        RemoteInputUpload(
                            name=WORKSPACE_RECEIPT_UPLOAD_NAME,
                            local_path=package.receipt_path,
                            relative_path=WORKSPACE_RECEIPT_REMOTE_PATH,
                        ),
                    )
            except Exception as exc:
                return _failed_response(
                    request,
                    code="code_workspace_resolution_failed",
                    message=str(exc),
                )
        remote_request_id = _remote_request_id(_canonical_request(worker_request))
        try:
            remote_request = RemoteJobRequest(
                request_id=remote_request_id,
                project=_safe_remote_label("project", request.project),
                run_id=_safe_remote_label("run", request.run_id),
                candidate_id=_safe_remote_label("candidate", request.candidate_id),
                workload_argv=self._workload_argv(),
                result_manifest_path=_REMOTE_RESPONSE_PATH,
                result_request_id=request.request_id,
                timeout_seconds=self.adapter_timeout_seconds + 30.0,
                heartbeat_interval_seconds=self.heartbeat_interval_seconds,
            )
        except ValueError as exc:
            return _failed_response(
                request,
                code="remote_adapter_configuration_invalid",
                message=str(exc),
            )

        try:
            with _adapter_request_file(worker_request) as request_path:
                submitted = await self.client.submit(
                    remote_request,
                    uploads=(
                        RemoteInputUpload(
                            name="adapter_request",
                            local_path=request_path,
                            relative_path=_REMOTE_REQUEST_PATH,
                        ),
                        *workspace_uploads,
                    ),
                )
            if submitted.request_id != remote_request_id:
                raise _RemoteAdapterFailure(
                    "remote_request_mismatch",
                    "submitted remote record has an unexpected request_id",
                    record=submitted,
                )
            terminal = await self._wait_for_terminal(submitted)
            fetched = await self.client.fetch(
                terminal.job_id,
                self._fetch_destination(request, terminal.job_id),
            )
            return _response_from_terminal(request, fetched)
        except _RemoteAdapterFailure as exc:
            return _failed_response(
                request,
                code=exc.code,
                message=str(exc),
                record=exc.record,
            )
        except Exception as exc:
            return _failed_response(
                request,
                code="remote_adapter_transport_failed",
                message=str(exc),
            )

    async def _wait_for_terminal(self, initial: RemoteJobRecord) -> RemoteJobRecord:
        record = initial
        deadline = self.monotonic() + self.max_wait_seconds
        consecutive_errors = 0
        while not record.state.terminal:
            if record.heartbeat_stale:
                raise _RemoteAdapterFailure(
                    "remote_heartbeat_stale",
                    f"remote job {record.job_id} heartbeat is stale",
                    record=record,
                )
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise _RemoteAdapterFailure(
                    "remote_poll_timeout",
                    f"remote job {record.job_id} exceeded {self.max_wait_seconds}s poll budget",
                    record=record,
                )
            await self.sleep(min(self.poll_interval_seconds, remaining))
            try:
                record = await self.client.status(record.job_id)
            except Exception as exc:
                consecutive_errors += 1
                if consecutive_errors > self.max_status_errors:
                    raise _RemoteAdapterFailure(
                        "remote_status_unavailable",
                        f"remote status remained unavailable: {str(exc)[-1000:]}",
                        record=record,
                    ) from exc
                continue
            consecutive_errors = 0
        return record

    def _workload_argv(self) -> tuple[str, ...]:
        return (
            self.remote_worker_python,
            "-m",
            _WORKER_MODULE,
            "--request-file",
            _REMOTE_REQUEST_PATH,
            "--output-file",
            _REMOTE_RESPONSE_PATH,
            "--timeout-seconds",
            str(self.adapter_timeout_seconds),
            "--",
            *self.trusted_adapter_argv,
        )

    def _fetch_destination(self, request: AdapterRequest, job_id: str) -> Path:
        root = self.artifact_root.expanduser().resolve()
        if not request.output_dir:
            return root / job_id
        requested = Path(request.output_dir).expanduser()
        if not requested.is_absolute():
            raise _RemoteAdapterFailure(
                "remote_output_path_invalid",
                "AdapterRequest.output_dir must be absolute for remote execution",
            )
        resolved = requested.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise _RemoteAdapterFailure(
                "remote_output_path_escape",
                "AdapterRequest.output_dir is outside the configured artifact root",
            ) from exc
        return resolved / "remote" / job_id


class _AdapterRequestFile:
    def __init__(self, request: AdapterRequest) -> None:
        import tempfile

        self._directory = tempfile.TemporaryDirectory(prefix="mars-remote-adapter-")
        self.path = Path(self._directory.name) / "adapter_request.json"
        self.path.write_text(request.model_dump_json(indent=2), encoding="utf-8")

    def __enter__(self) -> Path:
        return self.path

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        self._directory.cleanup()


def _adapter_request_file(request: AdapterRequest) -> _AdapterRequestFile:
    return _AdapterRequestFile(request)


def _safe_remote_label(prefix: str, value: str) -> str:
    normalized = value.strip()
    if normalized:
        try:
            return validate_identifier(normalized, label=prefix)
        except ValueError:
            pass
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _canonical_request(request: AdapterRequest) -> bytes:
    return json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _remote_request_id(canonical_request: bytes) -> str:
    digest = hashlib.sha256(canonical_request).hexdigest()
    return f"adapter-{digest[:32]}"


def _response_from_terminal(
    original: AdapterRequest,
    fetched: RemoteFetchResult,
) -> AdapterResponse:
    record = fetched.record
    response = record.adapter_response
    if response is None:
        return _failed_response(
            original,
            code="remote_response_missing",
            message=f"remote job {record.job_id} completed without AdapterResponse",
            record=record,
        )
    if response.request_id != original.request_id:
        return _failed_response(
            original,
            code="adapter_response_request_mismatch",
            message="remote AdapterResponse request_id does not match original request",
            record=record,
        )
    consistent = (
        record.state == RemoteJobState.SUCCEEDED
        and response.status in {"ready", "ok"}
    ) or (
        record.state == RemoteJobState.FAILED
        and response.status in {"blocked", "failed"}
    )
    if not consistent:
        return _failed_response(
            original,
            code="remote_terminal_state_failed",
            message=f"remote job ended in inconsistent state {record.state.value}",
            record=record,
        )

    downloaded = {item.name: item for item in fetched.downloaded}
    artifacts = dict(response.artifacts)
    for name in tuple(artifacts):
        local = downloaded.get(name)
        if local is not None:
            artifacts[name] = str(local.local_path)
    resource_usage = _canonical_remote_resource_usage(
        response.resource_usage,
        record,
    )
    return response.model_copy(
        update={
            "artifacts": artifacts,
            "resource_usage": resource_usage,
            "findings": (*response.findings, f"remote_job_id={record.job_id}"),
        }
    )


def _failed_response(
    request: AdapterRequest,
    *,
    code: str,
    message: str,
    status: str = "failed",
    record: RemoteJobRecord | None = None,
) -> AdapterResponse:
    resource_usage = (
        _canonical_remote_resource_usage(record.resource_usage.adapter, record)
        if record is not None
        else {}
    )
    findings = (f"remote_job_id={record.job_id}",) if record is not None else ()
    return AdapterResponse(
        request_id=request.request_id,
        status=status,
        resource_usage=resource_usage,
        findings=findings,
        error_code=code,
        error=message[-4000:],
    )


def _canonical_remote_resource_usage(
    adapter_usage: dict[str, float],
    record: RemoteJobRecord,
) -> dict[str, float]:
    """Expose authoritative remote allocation through canonical budget keys.

    Adapter self-report and executor allocation may overlap, so use the larger
    value instead of summing them.  The remote-specific keys remain available
    for provenance while generic ledgers consume wall_seconds/gpu_seconds.
    """

    usage = dict(adapter_usage)
    remote_wall = record.resource_usage.wall_seconds
    remote_gpu = record.resource_usage.allocated_gpu_seconds
    usage["remote_wall_seconds"] = remote_wall
    usage["remote_allocated_gpu_seconds"] = remote_gpu
    usage["wall_seconds"] = max(usage.get("wall_seconds", 0.0), remote_wall)
    usage["gpu_seconds"] = max(usage.get("gpu_seconds", 0.0), remote_gpu)
    return usage
