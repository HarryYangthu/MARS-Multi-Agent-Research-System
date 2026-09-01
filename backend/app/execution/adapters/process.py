"""Shell-free subprocess implementation of adapter.v1."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import stat
import tempfile

from app.execution.adapters.base import AdapterRequest, AdapterResponse
from app.execution.adapters.workspace import (
    WORKSPACE_ARCHIVE_REMOTE_PATH,
    WORKSPACE_CONFIG_KEY,
    WORKSPACE_RECEIPT_REMOTE_PATH,
    WorkspaceResolver,
    bind_workspace_request,
    workspace_binding_for_receipt,
)
from app.execution.subprocess_env import sanitized_subprocess_environment
from app.harness.discovery.code_workspace_transfer import (
    CodeWorkspaceTransferPackage,
    CodeWorkspaceTransferError,
    validate_code_workspace_transfer_package,
    verify_and_extract_code_workspace_transfer,
)

_MAX_WORKSPACE_RECEIPT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class ProcessAdapter:
    name: str
    argv: tuple[str, ...]
    timeout_seconds: float = 900.0
    env: dict[str, str] | None = None
    workspace_resolver: WorkspaceResolver | None = None
    workspace_required: bool = False

    def __post_init__(self) -> None:
        if not self.argv or not self.argv[0].strip():
            raise ValueError("adapter argv must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("adapter timeout must be positive")

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        if WORKSPACE_CONFIG_KEY in request.config:
            return _failure(
                request,
                code="local_workspace_binding_untrusted",
                message="workspace binding must be supplied by the trusted resolver",
            )
        if self.workspace_resolver is None:
            if self.workspace_required and request.candidate_id:
                return _failure(
                    request,
                    code="code_workspace_resolver_required",
                    message="a trusted workspace resolver is required for this candidate",
                )
            return await self._invoke_process(request, cwd=None)

        try:
            package = await self.workspace_resolver.resolve(request)
            if package is None:
                if self.workspace_required and request.candidate_id:
                    raise CodeWorkspaceTransferError(
                        "the trusted resolver did not provide a required code workspace"
                    )
                return await self._invoke_process(request, cwd=None)

            validate_code_workspace_transfer_package(package)
            expected_binding = workspace_binding_for_receipt(
                package.receipt,
                receipt_sha256=package.receipt_sha256,
            )
            if expected_binding.candidate_id != request.candidate_id:
                raise CodeWorkspaceTransferError(
                    "workspace candidate_id does not match AdapterRequest"
                )
            with tempfile.TemporaryDirectory(prefix="mars-code-workspace-") as raw_root:
                job_root = Path(raw_root)
                staged_archive, staged_receipt = _stage_workspace_inputs(
                    package,
                    job_root=job_root,
                )
                verified = verify_and_extract_code_workspace_transfer(
                    archive_path=staged_archive,
                    receipt_path=staged_receipt,
                    destination=job_root / expected_binding.relative_path,
                )
                actual_binding = workspace_binding_for_receipt(
                    verified.receipt,
                    receipt_sha256=verified.receipt_sha256,
                )
                if actual_binding != expected_binding:
                    raise CodeWorkspaceTransferError(
                        "verified workspace does not match resolver package"
                    )
                bound_request = bind_workspace_request(request, actual_binding)
                return await self._invoke_process(
                    bound_request,
                    cwd=job_root,
                )
        except Exception as exc:
            return _failure(
                request,
                code="code_workspace_resolution_failed",
                message=str(exc),
            )

    async def _invoke_process(
        self,
        request: AdapterRequest,
        *,
        cwd: Path | None,
    ) -> AdapterResponse:
        try:
            process = await asyncio.create_subprocess_exec(
                *self.argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=sanitized_subprocess_environment(
                    overrides={
                        **(self.env or {}),
                        # A code workspace stays below the private job cwd. Safe
                        # path is still mandatory defense in depth for trusted
                        # ``python -m`` adapters.
                        "PYTHONSAFEPATH": "1",
                    }
                ),
                cwd=cwd,
            )
        except OSError as exc:
            return _failure(
                request,
                code="adapter_process_launch_failed",
                message=str(exc),
            )
        payload = request.model_dump_json().encode("utf-8")
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return AdapterResponse(
                request_id=request.request_id,
                status="failed",
                error_code="adapter_timeout",
                error=f"adapter '{self.name}' exceeded {self.timeout_seconds}s",
            )
        if process.returncode != 0:
            return AdapterResponse(
                request_id=request.request_id,
                status="failed",
                error_code="adapter_process_failed",
                error=stderr.decode("utf-8", errors="replace")[-4000:],
            )
        try:
            raw = json.loads(stdout.decode("utf-8"))
            return AdapterResponse.model_validate(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return AdapterResponse(
                request_id=request.request_id,
                status="failed",
                error_code="adapter_invalid_response",
                error=str(exc),
            )


def _stage_workspace_inputs(
    package: CodeWorkspaceTransferPackage,
    *,
    job_root: Path,
) -> tuple[Path, Path]:
    """Copy resolver inputs to the two fixed job-owned paths before verification."""

    inputs = job_root / "inputs"
    inputs.mkdir(mode=0o700)
    archive = job_root.joinpath(*Path(WORKSPACE_ARCHIVE_REMOTE_PATH).parts)
    receipt = job_root.joinpath(*Path(WORKSPACE_RECEIPT_REMOTE_PATH).parts)
    _copy_bound_regular_file(
        package.archive_path,
        archive,
        label="workspace archive",
        expected_sha256=package.receipt.archive_sha256,
        expected_size=package.receipt.archive_size_bytes,
        max_bytes=package.receipt.archive_size_bytes,
    )
    _copy_bound_regular_file(
        package.receipt_path,
        receipt,
        label="workspace receipt",
        expected_sha256=package.receipt_sha256,
        expected_size=None,
        max_bytes=_MAX_WORKSPACE_RECEIPT_BYTES,
    )
    return archive, receipt


def _copy_bound_regular_file(
    source: Path,
    destination: Path,
    *,
    label: str,
    expected_sha256: str,
    expected_size: int | None,
    max_bytes: int,
) -> None:
    source_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
    try:
        source_descriptor = os.open(source, source_flags)
    except OSError as exc:
        raise CodeWorkspaceTransferError(f"cannot open {label}: {exc}") from exc
    destination_descriptor = -1
    published = False
    try:
        source_metadata = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_metadata.st_mode):
            raise CodeWorkspaceTransferError(f"{label} must be a regular file")
        if source_metadata.st_size > max_bytes:
            raise CodeWorkspaceTransferError(f"{label} exceeds byte limit")
        if expected_size is not None and source_metadata.st_size != expected_size:
            raise CodeWorkspaceTransferError(f"{label} size mismatch")
        try:
            destination_descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except OSError as exc:
            raise CodeWorkspaceTransferError(
                f"cannot stage {label}: {exc}"
            ) from exc
        digest = hashlib.sha256()
        copied = 0
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            if copied > max_bytes:
                raise CodeWorkspaceTransferError(f"{label} exceeds byte limit")
            digest.update(chunk)
            _write_all(destination_descriptor, chunk, label=label)
        if expected_size is not None and copied != expected_size:
            raise CodeWorkspaceTransferError(f"{label} size mismatch")
        actual_sha256 = f"sha256:{digest.hexdigest()}"
        if actual_sha256 != expected_sha256:
            raise CodeWorkspaceTransferError(f"{label} hash mismatch")
        os.fsync(destination_descriptor)
        if hasattr(os, "fchmod"):
            os.fchmod(destination_descriptor, 0o644)
        else:  # Windows exposes chmod by path rather than file descriptor.
            destination.chmod(0o644)
        published = True
    finally:
        os.close(source_descriptor)
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        if not published:
            destination.unlink(missing_ok=True)


def _write_all(descriptor: int, payload: bytes, *, label: str) -> None:
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(descriptor, payload[offset:])
        except OSError as exc:
            raise CodeWorkspaceTransferError(f"cannot stage {label}: {exc}") from exc
        if written <= 0:
            raise CodeWorkspaceTransferError(f"cannot stage {label}: short write")
        offset += written


def _failure(
    request: AdapterRequest,
    *,
    code: str,
    message: str,
) -> AdapterResponse:
    return AdapterResponse(
        request_id=request.request_id,
        status="failed",
        error_code=code,
        error=message[-4000:],
    )
