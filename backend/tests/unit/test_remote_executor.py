from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path

import pytest

from app.execution.remote.executor import (
    RemoteExecutionError,
    RemoteExecutor,
    RemoteExecutorConfig,
    RemoteInputUpload,
    derive_remote_job_id,
    load_remote_executor_config,
)
from app.execution.remote.records import (
    RemoteJobRecord,
    RemoteJobRequest,
    RemoteJobState,
    RemoteOutputArtifact,
)
from app.execution.remote.transport import SystemSshTransport, TransportResult


class FakeTransport:
    def __init__(self, *, now: datetime) -> None:
        self.now = now
        self.runs: list[tuple[str, ...]] = []
        self.uploads: dict[str, bytes] = {}
        self.downloads: dict[str, bytes] = {}
        self.record: RemoteJobRecord | None = None
        self.fail_next_status = False

    def local_findings(self) -> tuple[str, ...]:
        return ()

    async def run(
        self,
        remote_argv: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> TransportResult:
        assert timeout_seconds > 0
        self.runs.append(remote_argv)
        action = remote_argv[5]
        if action == "readiness":
            return TransportResult(
                argv=remote_argv,
                returncode=0,
                stdout='{"protocol":"remote_readiness.v1","status":"ready","runner_version":"test"}',
            )
        if action == "stage":
            return TransportResult(argv=remote_argv, returncode=0, stdout="{}")
        if action == "submit":
            job_id = remote_argv[7]
            request_sha256 = remote_argv[9]
            request_path = next(
                path for path in self.uploads if path.endswith(f"/{job_id}/request.json")
            )
            request = RemoteJobRequest.model_validate_json(self.uploads[request_path])
            if self.record is None:
                self.record = _record(
                    request=request,
                    job_id=job_id,
                    request_sha256=request_sha256,
                    now=self.now,
                )
            return TransportResult(
                argv=remote_argv,
                returncode=0,
                stdout=self.record.model_dump_json(),
            )
        if self.record is None:
            raise AssertionError("test must submit before control operations")
        if action == "status" and self.fail_next_status:
            self.fail_next_status = False
            return TransportResult(
                argv=remote_argv,
                returncode=255,
                stderr="temporary SSH disconnect",
            )
        if action == "cancel":
            self.record = self.record.model_copy(
                update={"state": RemoteJobState.CANCELLED, "finished_at": self.now}
            )
        return TransportResult(
            argv=remote_argv,
            returncode=0,
            stdout=self.record.model_dump_json(),
        )

    async def upload(
        self,
        local_path: Path,
        remote_path: str,
        *,
        timeout_seconds: float,
    ) -> TransportResult:
        assert timeout_seconds > 0
        self.uploads[remote_path] = local_path.read_bytes()
        return TransportResult(argv=("upload",), returncode=0)

    async def download(
        self,
        remote_path: str,
        local_path: Path,
        *,
        timeout_seconds: float,
    ) -> TransportResult:
        assert timeout_seconds > 0
        local_path.write_bytes(self.downloads[remote_path])
        return TransportResult(argv=("download",), returncode=0)


def _config(tmp_path: Path) -> RemoteExecutorConfig:
    return RemoteExecutorConfig(
        enabled=True,
        host="gpu.example.test",
        port=2200,
        user="mars",
        key_path=tmp_path / "id_ed25519",
        known_hosts_path=tmp_path / "known_hosts",
        remote_root="/srv/mars",
        python="/opt/mars/bin/python",
        gpu_ids=("0", "1"),
        heartbeat_stale_seconds=60.0,
    )


def _record(
    *,
    request: RemoteJobRequest,
    job_id: str,
    request_sha256: str,
    now: datetime,
) -> RemoteJobRecord:
    return RemoteJobRecord(
        request_id=request.request_id,
        job_id=job_id,
        request_sha256=request_sha256,
        state=RemoteJobState.RUNNING,
        submitted_at=now,
        started_at=now,
        heartbeat_at=now,
        worker_pid=1234,
    )


@pytest.mark.asyncio
async def test_submit_is_idempotent_and_keeps_candidate_payload_out_of_ssh_argv(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 25, 4, 0, tzinfo=timezone.utc)
    transport = FakeTransport(now=now)
    executor = RemoteExecutor(_config(tmp_path), transport=transport, clock=lambda: now)
    candidate_marker = "candidate-source-marker"
    bundle = tmp_path / "candidate.tar.zst"
    bundle.write_bytes(f"candidate archive: {candidate_marker}".encode())
    request = RemoteJobRequest(
        request_id="request-001",
        project="pimc",
        run_id="run-001",
        candidate_id="candidate-001",
        workload_argv=(
            "/opt/mars/bin/pimc_adapter",
            "--candidate-bundle",
            "inputs/candidate.tar.zst",
        ),
        heartbeat_interval_seconds=1.0,
    )
    upload = RemoteInputUpload(
        name="candidate_bundle",
        local_path=bundle,
        relative_path="inputs/candidate.tar.zst",
    )

    readiness = await executor.readiness()
    first = await executor.submit(request, uploads=(upload,))
    second = await executor.submit(request, uploads=(upload,))

    assert readiness.status == "ready"
    assert first.job_id == second.job_id == derive_remote_job_id(request.request_id)
    assert first.request_sha256 == second.request_sha256
    assert all(argv[:3] == ("/opt/mars/bin/python", "-m", "app.execution.remote.runner") for argv in transport.runs)
    assert candidate_marker not in "\n".join(" ".join(argv) for argv in transport.runs)
    uploaded_request = RemoteJobRequest.model_validate_json(
        next(value for path, value in transport.uploads.items() if path.endswith("request.json"))
    )
    assert uploaded_request.gpu_ids == ("0", "1")
    assert uploaded_request.input_artifacts[0].sha256 == hashlib.sha256(bundle.read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_status_marks_stale_heartbeat_without_changing_remote_state(
    tmp_path: Path,
) -> None:
    heartbeat = datetime(2026, 8, 25, 4, 0, tzinfo=timezone.utc)
    now = datetime(2026, 8, 25, 4, 2, tzinfo=timezone.utc)
    transport = FakeTransport(now=heartbeat)
    executor = RemoteExecutor(_config(tmp_path), transport=transport, clock=lambda: now)
    request = RemoteJobRequest(
        request_id="request-stale",
        project="pimc",
        run_id="run-stale",
        candidate_id="candidate-stale",
        workload_argv=("/opt/mars/bin/pimc_adapter",),
    )
    submitted = await executor.submit(request)

    status = await executor.status(submitted.job_id)

    assert status.state == RemoteJobState.RUNNING
    assert status.heartbeat_stale is True


@pytest.mark.asyncio
async def test_transient_disconnect_does_not_destroy_recoverable_remote_job(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 25, 4, 0, tzinfo=timezone.utc)
    transport = FakeTransport(now=now)
    executor = RemoteExecutor(_config(tmp_path), transport=transport, clock=lambda: now)
    request = RemoteJobRequest(
        request_id="request-recover",
        project="pimc",
        run_id="run-recover",
        candidate_id="candidate-recover",
        workload_argv=("/opt/mars/bin/pimc_adapter",),
    )
    submitted = await executor.submit(request)
    transport.fail_next_status = True

    with pytest.raises(RemoteExecutionError, match="temporary SSH disconnect"):
        await executor.status(submitted.job_id)
    recovered = await executor.status_for_request(request.request_id)

    assert recovered.job_id == submitted.job_id
    assert recovered.state == RemoteJobState.RUNNING


@pytest.mark.asyncio
async def test_fetch_verifies_and_moves_remote_artifacts(tmp_path: Path) -> None:
    now = datetime(2026, 8, 25, 4, 0, tzinfo=timezone.utc)
    transport = FakeTransport(now=now)
    executor = RemoteExecutor(_config(tmp_path), transport=transport, clock=lambda: now)
    request = RemoteJobRequest(
        request_id="request-fetch",
        project="pimc",
        run_id="run-fetch",
        candidate_id="candidate-fetch",
        workload_argv=("/opt/mars/bin/pimc_adapter",),
    )
    submitted = await executor.submit(request)
    payload = b'{"metric": -27.2}'
    artifact = RemoteOutputArtifact(
        name="result",
        relative_path="artifacts/result.json",
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )
    assert transport.record is not None
    transport.record = transport.record.model_copy(
        update={
            "state": RemoteJobState.SUCCEEDED,
            "finished_at": now,
            "artifacts": (artifact,),
        }
    )
    remote_path = f"/srv/mars/jobs/{submitted.job_id}/{artifact.relative_path}"
    transport.downloads[remote_path] = payload

    result = await executor.fetch(submitted.job_id, tmp_path / "download")

    assert result.record.state == RemoteJobState.SUCCEEDED
    assert result.downloaded[0].local_path.read_bytes() == payload
    assert not result.downloaded[0].local_path.with_suffix(".json.part").exists()


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


@pytest.mark.asyncio
async def test_system_transport_uses_argv_and_strict_host_key_checking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run(
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> TransportResult:
        assert timeout_seconds == 9.0
        calls.append(argv)
        return TransportResult(argv=argv, returncode=0)

    monkeypatch.setattr("app.execution.remote.transport._run_argv", fake_run)
    transport = SystemSshTransport(
        host="2001:db8::1",
        port=2222,
        user="mars",
        key_path=tmp_path / "key",
        known_hosts_path=tmp_path / "known_hosts",
    )
    local = tmp_path / "request.json"
    await transport.run(("python3", "-m", "app.execution.remote.runner"), timeout_seconds=9.0)
    await transport.upload(local, "/srv/mars/request.json", timeout_seconds=9.0)
    await transport.download("/srv/mars/result.json", local, timeout_seconds=9.0)

    assert calls[0][0] == "ssh"
    assert "StrictHostKeyChecking=yes" in calls[0]
    assert calls[1][0] == "scp"
    assert "mars@[2001:db8::1]:/srv/mars/request.json" in calls[1]
    assert calls[2][-1] == str(local)
