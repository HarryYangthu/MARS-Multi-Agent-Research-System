"""Fixed, shell-free worker that bridges a remote job to adapter.v1."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from pathlib import Path

from app.execution.adapters.base import AdapterRequest, AdapterResponse
from app.execution.adapters.workspace import (
    WORKSPACE_ARCHIVE_REMOTE_PATH,
    WORKSPACE_RECEIPT_REMOTE_PATH,
    workspace_binding_for_receipt,
    workspace_binding_from_request,
)
from app.execution.subprocess_env import sanitized_subprocess_environment
from app.execution.remote.records import RemoteJobRequest, validate_relative_path
from app.harness.discovery.code_workspace_transfer import (
    CodeWorkspaceTransferError,
    verify_and_extract_code_workspace_transfer,
)


async def invoke_trusted_adapter(
    request: AdapterRequest,
    trusted_argv: tuple[str, ...],
    *,
    timeout_seconds: float,
    cwd: Path | None = None,
) -> AdapterResponse:
    """Invoke one composition-owned adapter argv and validate its response."""

    if timeout_seconds <= 0:
        raise ValueError("adapter timeout must be positive")
    _validate_trusted_argv(trusted_argv)
    try:
        process = await asyncio.create_subprocess_exec(
            *trusted_argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=sanitized_subprocess_environment(
                overrides={"PYTHONSAFEPATH": "1"}
            ),
        )
    except OSError as exc:
        return _failure(
            request,
            code="adapter_process_launch_failed",
            message=str(exc),
        )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(request.model_dump_json().encode("utf-8")),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        return _failure(
            request,
            code="adapter_timeout",
            message=f"trusted adapter exceeded {timeout_seconds}s",
        )

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace")[-4000:]
        return _failure(
            request,
            code="adapter_process_failed",
            message=detail or f"trusted adapter exited {process.returncode}",
        )
    try:
        response = AdapterResponse.model_validate_json(stdout)
    except ValueError as exc:
        return _failure(
            request,
            code="adapter_invalid_response",
            message=str(exc),
        )
    if response.request_id != request.request_id:
        return _failure(
            request,
            code="adapter_response_request_mismatch",
            message="trusted adapter changed AdapterResponse.request_id",
        )
    return response


async def run_worker(
    *,
    request_file: str,
    output_file: str,
    trusted_argv: tuple[str, ...],
    timeout_seconds: float,
    job_root: Path | None = None,
) -> AdapterResponse:
    """Read the uploaded request and atomically write ``response.json``."""

    root = (job_root or Path.cwd()).resolve(strict=True)
    request_path = _job_path(
        root,
        request_file,
        label="request file",
        require_exists=True,
    )
    output_path = _job_path(
        root,
        output_file,
        label="output file",
        require_exists=False,
    )
    request = AdapterRequest.model_validate_json(
        request_path.read_text(encoding="utf-8")
    )
    # All trusted remote adapters receive the job-owned absolute artifact
    # destination, including config-only and readiness requests.
    adapter_request = request.model_copy(
        update={"output_dir": str(root / "artifacts")}
    )
    try:
        binding = workspace_binding_from_request(request)
        if binding is not None:
            archive_path = _job_path(
                root,
                WORKSPACE_ARCHIVE_REMOTE_PATH,
                label="workspace archive",
                require_exists=True,
            )
            receipt_path = _job_path(
                root,
                WORKSPACE_RECEIPT_REMOTE_PATH,
                label="workspace receipt",
                require_exists=True,
            )
            verified = verify_and_extract_code_workspace_transfer(
                archive_path=archive_path,
                receipt_path=receipt_path,
                destination=root / binding.relative_path,
            )
            actual_binding = workspace_binding_for_receipt(
                verified.receipt,
                receipt_sha256=verified.receipt_sha256,
            )
            if actual_binding != binding:
                raise CodeWorkspaceTransferError(
                    "verified workspace does not match AdapterRequest binding"
                )
            if verified.receipt.candidate_id != request.candidate_id:
                raise CodeWorkspaceTransferError(
                    "verified workspace candidate_id does not match AdapterRequest"
                )
            # The trusted adapter executes at the job root while candidate
            # code stays below the verified ``workspace/`` binding. Remote
            # artifacts also remain rooted in the job directory so the
            # executor can fetch the declared ``artifacts/**`` outputs.  The
            # job root also makes binding.relative_path == "workspace"
            # directly usable by the trusted adapter without importing code
            # implicitly from the candidate directory.
    except (CodeWorkspaceTransferError, OSError, ValueError) as exc:
        response = _failure(
            request,
            code="workspace_verification_failed",
            message=str(exc),
        )
        _atomic_write_response(output_path, response)
        return response
    response = await invoke_trusted_adapter(
        adapter_request,
        trusted_argv,
        timeout_seconds=timeout_seconds,
        cwd=root,
    )
    _atomic_write_response(output_path, response)
    return response


def _validate_trusted_argv(argv: tuple[str, ...]) -> None:
    if not argv:
        raise ValueError("trusted adapter argv must not be empty")
    # Reuse remote_job.v1's bounded-token, no-shell, no-inline-eval contract.
    RemoteJobRequest(
        request_id="adapter-worker-validation",
        project="validation",
        run_id="validation",
        candidate_id="validation",
        workload_argv=argv,
    )


def _job_path(
    root: Path,
    raw: str,
    *,
    label: str,
    require_exists: bool,
) -> Path:
    normalized = validate_relative_path(raw, label=label)
    candidate = root / normalized
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link")
    resolved = candidate.resolve(strict=require_exists)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the remote job directory") from exc
    if require_exists and not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _atomic_write_response(path: Path, response: AdapterResponse) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(response.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mars_remote_adapter_worker")
    parser.add_argument("--request-file", required=True)
    parser.add_argument("--output-file", required=True)
    parser.add_argument("--timeout-seconds", required=True, type=float)
    parser.add_argument("adapter_argv", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    adapter_argv = tuple(str(item) for item in args.adapter_argv)
    if adapter_argv[:1] == ("--",):
        adapter_argv = adapter_argv[1:]
    try:
        asyncio.run(
            run_worker(
                request_file=str(args.request_file),
                output_file=str(args.output_file),
                trusted_argv=adapter_argv,
                timeout_seconds=float(args.timeout_seconds),
            )
        )
    except (OSError, ValueError) as exc:
        error = json.dumps(
            {"error_code": "remote_adapter_worker_invalid", "error": str(exc)},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        os.write(2, (error + "\n").encode("utf-8", errors="replace"))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
