"""Remote executor contracts use actual files and OpenSSH failures, never fabricated jobs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import socket

import pytest

from app.execution.remote.executor import (
    RemoteExecutionError, RemoteExecutor, RemoteExecutorConfig, RemoteInputUpload,
    _input_artifact, derive_remote_job_id, load_remote_executor_config,
)
from app.execution.remote.records import RemoteJobRecord, RemoteJobRequest, RemoteJobState
from app.execution.remote.transport import SystemSshTransport


@pytest.mark.asyncio
async def test_disabled_or_missing_remote_configuration_never_reports_ready(tmp_path: Path) -> None:
    for config in (RemoteExecutorConfig(), RemoteExecutorConfig(enabled=True)):
        executor = RemoteExecutor(config)
        readiness = await executor.readiness()
        assert readiness.status == "blocked"
        request = RemoteJobRequest(request_id="authored-request", project="pimc", run_id="run",
                                   candidate_id="candidate", workload_argv=("python3", "-m", "approved_adapter"))
        with pytest.raises(RemoteExecutionError):
            await executor.submit(request)
    assert not list(tmp_path.rglob("*"))


def test_input_receipt_hashes_actual_bytes_and_rejects_missing_files(tmp_path: Path) -> None:
    path = tmp_path / "input.txt"
    path.write_bytes(b"actual transfer input")
    upload = RemoteInputUpload(name="input", local_path=path, relative_path="inputs/input.txt")
    artifact = _input_artifact(upload)
    assert artifact.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert artifact.size_bytes == path.stat().st_size
    assert derive_remote_job_id("request-1") == derive_remote_job_id("request-1")
    assert derive_remote_job_id("request-1") != derive_remote_job_id("request-2")
    path.unlink()
    with pytest.raises(RemoteExecutionError):
        _input_artifact(upload)


def test_stale_status_computation_does_not_invent_a_remote_transition() -> None:
    # Authored timestamp input to the pure status calculation, not a fetched job.
    now = datetime.now(timezone.utc)
    original = RemoteJobRecord(request_id="authored-status", job_id=derive_remote_job_id("authored-status"),
                               request_sha256="a"*64, state=RemoteJobState.RUNNING,
                               submitted_at=now-timedelta(seconds=120), heartbeat_at=now-timedelta(seconds=120))
    executor = RemoteExecutor(RemoteExecutorConfig(heartbeat_stale_seconds=60))
    result = executor._with_stale_status(original)
    assert result.heartbeat_stale is True and result.state == RemoteJobState.RUNNING
    assert original.heartbeat_stale is False


@pytest.mark.parametrize("relative", ["../outside", "/tmp/outside", "inputs/../../outside", "inputs/nested/file"])
def test_upload_paths_cannot_escape_single_input_directory(relative: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        RemoteInputUpload(name="input", local_path=tmp_path / "input", relative_path=relative)


@pytest.mark.asyncio
async def test_actual_openssh_connection_refusal_keeps_strict_host_key_options(tmp_path: Path) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        transport = SystemSshTransport(host="127.0.0.1", port=sock.getsockname()[1], user="mars",
                                      key_path=tmp_path / "absent-key", known_hosts_path=tmp_path / "absent-known-hosts",
                                      connect_timeout_seconds=1)
        result = await transport.run(("python3", "-m", "app.execution.remote.runner"), timeout_seconds=5)
    assert result.returncode != 0
    assert "StrictHostKeyChecking=yes" in result.argv and "BatchMode=yes" in result.argv
    assert not list(tmp_path.rglob("*"))
    ipv6 = SystemSshTransport(host="2001:db8::1", port=2222, user="mars",
                              key_path=tmp_path / "key", known_hosts_path=tmp_path / "known")
    assert ipv6._scp_target() == "mars@[2001:db8::1]"
    assert ipv6._scp_options()[0] == "-P"
    assert "StrictHostKeyChecking=yes" in ipv6._scp_options()
    assert ipv6.local_findings()


def test_config_resolves_connection_values_from_named_environment(tmp_path: Path) -> None:
    config_path = tmp_path / "execution.yaml"
    config_path.write_text(
        """
execution:
  remote_gpu:
    enabled: false
    env:
      enabled: REMOTE_ON
      host: REMOTE_HOST
      port: REMOTE_PORT
      user: REMOTE_USER
      key_path: REMOTE_KEY
      known_hosts: REMOTE_KNOWN
      remote_root: REMOTE_ROOT
      python: REMOTE_PYTHON
      gpu_ids: REMOTE_GPUS
    heartbeat_stale_seconds: 123
""".strip(),
        encoding="utf-8",
    )

    config = load_remote_executor_config(
        config_path,
        environ={
            "REMOTE_ON": "true",
            "REMOTE_HOST": "gpu.example.test",
            "REMOTE_PORT": "2222",
            "REMOTE_USER": "mars",
            "REMOTE_KEY": "/secure/id_ed25519",
            "REMOTE_KNOWN": "/secure/known_hosts",
            "REMOTE_ROOT": "/srv/mars",
            "REMOTE_PYTHON": "/opt/mars/bin/python",
            "REMOTE_GPUS": "0,2",
        },
    )

    assert config.enabled is True
    assert config.port == 2222
    assert config.gpu_ids == ("0", "2")
    assert config.heartbeat_stale_seconds == 123.0


def test_remote_request_rejects_inline_code_workloads() -> None:
    with pytest.raises(ValueError, match="inline code evaluation"):
        RemoteJobRequest(
            request_id="request-inline",
            project="pimc",
            run_id="run-inline",
            candidate_id="candidate-inline",
            workload_argv=("python3", "-c", "pass"),
        )
