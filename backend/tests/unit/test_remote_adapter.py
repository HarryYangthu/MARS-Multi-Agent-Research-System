from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys

import pytest

from app.execution.adapters.base import AdapterAction, AdapterRequest, AdapterResponse
from app.execution.adapters.workspace import (
    WORKSPACE_ARCHIVE_REMOTE_PATH,
    WORKSPACE_ARCHIVE_UPLOAD_NAME,
    WORKSPACE_RECEIPT_REMOTE_PATH,
    WORKSPACE_RECEIPT_UPLOAD_NAME,
    bind_workspace_request,
    workspace_binding_for_receipt,
    workspace_binding_from_request,
)
from app.execution.remote.adapter import RemoteProjectAdapter
from app.execution.remote.adapter_worker import run_worker
from app.execution.remote.executor import RemoteInputUpload
from app.execution.remote.records import (
    RemoteFetchResult,
    RemoteJobRecord,
    RemoteJobRequest,
    RemoteJobState,
    RemoteReadiness,
    RemoteResourceUsage,
    derive_remote_job_id,
)
from app.harness.discovery.code_materialization import (
    CodeBlobOperation,
    CodeMaterializationBundle,
    content_blob_path,
    content_sha256,
    materialize_code_workspace,
)
from app.harness.discovery.code_workspace_transfer import (
    CodeWorkspaceTransferPackage,
    build_code_workspace_transfer,
)
from app.harness.discovery.snapshots import SnapshotPolicy, create_snapshot


class FakeRemoteClient:
    def __init__(self) -> None:
        self.remote_request: RemoteJobRequest | None = None
        self.remote_request_ids: list[str] = []
        self.uploaded_request: AdapterRequest | None = None
        self.uploads: tuple[RemoteInputUpload, ...] = ()
        self.record: RemoteJobRecord | None = None

    async def readiness(self) -> RemoteReadiness:
        return RemoteReadiness(status="ready", runner_version="test")

    async def submit(
        self,
        request: RemoteJobRequest,
        *,
        uploads: tuple[RemoteInputUpload, ...] = (),
    ) -> RemoteJobRecord:
        self.remote_request = request
        self.remote_request_ids.append(request.request_id)
        self.uploads = uploads
        upload = next(item for item in uploads if item.name == "adapter_request")
        self.uploaded_request = AdapterRequest.model_validate_json(
            upload.local_path.read_text(encoding="utf-8")
        )
        now = datetime(2026, 8, 25, tzinfo=timezone.utc)
        self.record = RemoteJobRecord(
            request_id=request.request_id,
            job_id=derive_remote_job_id(request.request_id),
            request_sha256="a" * 64,
            state=RemoteJobState.RUNNING,
            submitted_at=now,
            started_at=now,
            heartbeat_at=now,
        )
        return self.record

    async def status(self, job_id: str) -> RemoteJobRecord:
        assert self.record is not None
        assert self.uploaded_request is not None
        assert job_id == self.record.job_id
        self.record = self.record.model_copy(
            update={
                "state": RemoteJobState.SUCCEEDED,
                "finished_at": datetime(2026, 8, 25, tzinfo=timezone.utc),
                "adapter_response": AdapterResponse(
                    request_id=self.uploaded_request.request_id,
                    status="ok",
                    raw_metrics={"RES": -27.2},
                    resource_usage={"wall_seconds": 2.0, "gpu_seconds": 3.0},
                ),
                "resource_usage": RemoteResourceUsage(
                    wall_seconds=5.0,
                    allocated_gpu_seconds=10.0,
                    adapter={"wall_seconds": 2.0, "gpu_seconds": 3.0},
                ),
            }
        )
        return self.record

    async def fetch(self, job_id: str, destination: Path) -> RemoteFetchResult:
        assert self.record is not None
        assert job_id == self.record.job_id
        return RemoteFetchResult(record=self.record)


async def _no_sleep(_seconds: float) -> None:
    return None


class _StaticWorkspaceResolver:
    def __init__(self, package: CodeWorkspaceTransferPackage) -> None:
        self.package = package

    async def resolve(
        self,
        _request: AdapterRequest,
    ) -> CodeWorkspaceTransferPackage:
        return self.package


@pytest.mark.asyncio
async def test_adapter_worker_runs_trusted_process_over_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-reach-remote-pack")
    monkeypatch.setenv("MARS_REMOTE_SSH_HOST", "must-not-reach-remote-pack")
    adapter_script = tmp_path / "trusted_adapter.py"
    adapter_script.write_text(
        "import json, os, pathlib, sys\n"
        "request = json.load(sys.stdin)\n"
        "assert 'DEEPSEEK_API_KEY' not in os.environ\n"
        "assert 'MARS_REMOTE_SSH_HOST' not in os.environ\n"
        "output = pathlib.Path(request['output_dir'])\n"
        "assert output.is_absolute()\n"
        "assert output.parent == pathlib.Path.cwd()\n"
        "output.mkdir(parents=True, exist_ok=True)\n"
        "(output / 'config-only-called').write_text('yes', encoding='utf-8')\n"
        "response = {\n"
        "    'protocol': 'adapter.v1',\n"
        "    'request_id': request['request_id'],\n"
        "    'status': 'ok',\n"
        "    'raw_metrics': {'RES': -27.2},\n"
        "}\n"
        "sys.stdout.write(json.dumps(response))\n",
        encoding="utf-8",
    )
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    request = AdapterRequest(
        action=AdapterAction.EVALUATE,
        request_id="evaluate:0:candidate",
        project="pimc",
        run_id="run-1",
        candidate_id="candidate-1",
        config={"candidate_secret_marker": "uploaded-only"},
    )
    (inputs / "adapter_request.json").write_text(
        request.model_dump_json(),
        encoding="utf-8",
    )

    response = await run_worker(
        request_file="inputs/adapter_request.json",
        output_file="response.json",
        trusted_argv=(sys.executable, str(adapter_script)),
        timeout_seconds=5.0,
        job_root=tmp_path,
    )

    assert response.request_id == request.request_id
    assert response.status == "ok"
    assert (tmp_path / "artifacts" / "config-only-called").is_file()
    persisted = AdapterResponse.model_validate_json(
        (tmp_path / "response.json").read_text(encoding="utf-8")
    )
    assert persisted == response


@pytest.mark.asyncio
async def test_remote_project_adapter_preserves_original_request_id_and_payload(
    tmp_path: Path,
) -> None:
    client = FakeRemoteClient()
    adapter = RemoteProjectAdapter(
        name="pimc-remote",
        client=client,
        trusted_adapter_argv=("/opt/mars/bin/python", "-m", "pimc_adapter"),
        artifact_root=tmp_path / "artifacts",
        poll_interval_seconds=0.01,
        sleep=_no_sleep,
    )
    request = AdapterRequest(
        action=AdapterAction.EVALUATE,
        request_id="evaluate:0:candidate-with-colons",
        project="pimc",
        run_id="run/unsafe-for-paths",
        candidate_id="candidate/unsafe-for-paths",
        config={"candidate_source_marker": "must-not-enter-argv"},
    )

    response = await adapter.invoke(request)

    assert response.status == "ok"
    assert response.request_id == request.request_id
    assert response.resource_usage["wall_seconds"] == 5.0
    assert response.resource_usage["gpu_seconds"] == 10.0
    assert response.resource_usage["remote_wall_seconds"] == 5.0
    assert response.resource_usage["remote_allocated_gpu_seconds"] == 10.0
    assert client.remote_request is not None
    assert client.remote_request.request_id.startswith("adapter-")
    assert ":" not in client.remote_request.request_id
    assert client.remote_request.result_request_id == request.request_id
    assert "must-not-enter-argv" not in " ".join(
        client.remote_request.workload_argv
    )
    assert client.uploaded_request is not None
    assert client.uploaded_request.config == request.config
    assert client.uploaded_request.output_dir == "artifacts"


def test_remote_project_adapter_rejects_shell_as_inner_adapter(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="shell interpreters"):
        RemoteProjectAdapter(
            name="unsafe",
            client=FakeRemoteClient(),
            trusted_adapter_argv=("bash", "adapter.sh"),
            artifact_root=tmp_path,
        )


@pytest.mark.asyncio
async def test_remote_adapter_uploads_bound_workspace_and_content_keys_job_id(
    tmp_path: Path,
) -> None:
    first_package = _workspace_package(tmp_path / "first", replacement_version=2)
    second_package = _workspace_package(tmp_path / "second", replacement_version=3)
    request = AdapterRequest(
        action=AdapterAction.EVALUATE,
        request_id="evaluate:workspace:candidate-1",
        project="pimc",
        run_id="run-1",
        candidate_id="candidate-1",
    )

    first_client = FakeRemoteClient()
    first_adapter = RemoteProjectAdapter(
        name="pimc-remote",
        client=first_client,
        trusted_adapter_argv=("/opt/mars/bin/python", "-m", "pimc_adapter"),
        artifact_root=tmp_path / "artifacts",
        workspace_resolver=_StaticWorkspaceResolver(first_package),
        poll_interval_seconds=0.01,
        sleep=_no_sleep,
    )
    assert (await first_adapter.invoke(request)).status == "ok"
    assert (await first_adapter.invoke(request)).status == "ok"

    assert tuple(item.name for item in first_client.uploads) == (
        "adapter_request",
        WORKSPACE_ARCHIVE_UPLOAD_NAME,
        WORKSPACE_RECEIPT_UPLOAD_NAME,
    )
    assert tuple(item.relative_path for item in first_client.uploads) == (
        "inputs/adapter_request.json",
        WORKSPACE_ARCHIVE_REMOTE_PATH,
        WORKSPACE_RECEIPT_REMOTE_PATH,
    )
    assert first_client.uploaded_request is not None
    first_binding = workspace_binding_from_request(first_client.uploaded_request)
    assert first_binding is not None
    assert first_binding.relative_path == "workspace"
    assert first_binding.archive_sha256 == first_package.receipt.archive_sha256
    assert first_client.remote_request_ids[0] == first_client.remote_request_ids[1]

    second_client = FakeRemoteClient()
    second_adapter = RemoteProjectAdapter(
        name="pimc-remote",
        client=second_client,
        trusted_adapter_argv=("/opt/mars/bin/python", "-m", "pimc_adapter"),
        artifact_root=tmp_path / "artifacts",
        workspace_resolver=_StaticWorkspaceResolver(second_package),
        poll_interval_seconds=0.01,
        sleep=_no_sleep,
    )
    assert (await second_adapter.invoke(request)).status == "ok"
    assert second_client.remote_request is not None
    assert first_client.remote_request is not None
    assert second_client.remote_request.request_id != first_client.remote_request.request_id


@pytest.mark.asyncio
async def test_worker_strongly_verifies_workspace_before_launching_adapter(
    tmp_path: Path,
) -> None:
    package = _workspace_package(tmp_path / "package", replacement_version=2)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    shutil.copyfile(package.archive_path, inputs / "code_workspace.tar")
    shutil.copyfile(package.receipt_path, inputs / "code_workspace_receipt.json")
    binding = workspace_binding_for_receipt(
        package.receipt,
        receipt_sha256=package.receipt_sha256,
    )
    request = bind_workspace_request(
        AdapterRequest(
            action=AdapterAction.EVALUATE,
            request_id="evaluate:workspace:candidate-1",
            project="pimc",
            run_id="run-1",
            candidate_id="candidate-1",
        ),
        binding,
    )
    (inputs / "adapter_request.json").write_text(
        request.model_dump_json(),
        encoding="utf-8",
    )
    adapter_script = tmp_path / "workspace_adapter.py"
    adapter_script.write_text(
        "import json, pathlib, sys\n"
        "request = json.load(sys.stdin)\n"
        "binding = request['config']['_mars_code_workspace']\n"
        "assert pathlib.Path(binding['relative_path'], 'model.py').is_file()\n"
        "output = pathlib.Path(request['output_dir'])\n"
        "assert output.is_absolute()\n"
        "assert output.parent == pathlib.Path.cwd()\n"
        "output.mkdir(parents=True, exist_ok=True)\n"
        "(output / 'adapter-called').write_text('yes', encoding='utf-8')\n"
        "sys.stdout.write(json.dumps({\n"
        "    'protocol': 'adapter.v1',\n"
        "    'request_id': request['request_id'],\n"
        "    'status': 'ok',\n"
        "}))\n",
        encoding="utf-8",
    )

    response = await run_worker(
        request_file="inputs/adapter_request.json",
        output_file="response.json",
        trusted_argv=(sys.executable, str(adapter_script)),
        timeout_seconds=5.0,
        job_root=tmp_path,
    )

    assert response.status == "ok"
    assert (tmp_path / "artifacts" / "adapter-called").read_text(
        encoding="utf-8"
    ) == "yes"
    assert (tmp_path / "workspace" / "model.py").is_file()
    assert not (tmp_path / "workspace" / "artifacts").exists()


@pytest.mark.asyncio
async def test_worker_does_not_launch_adapter_after_workspace_tampering(
    tmp_path: Path,
) -> None:
    package = _workspace_package(tmp_path / "package", replacement_version=2)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    archive_path = inputs / "code_workspace.tar"
    shutil.copyfile(package.archive_path, archive_path)
    shutil.copyfile(package.receipt_path, inputs / "code_workspace_receipt.json")
    payload = bytearray(archive_path.read_bytes())
    payload[512] ^= 1
    archive_path.write_bytes(payload)
    binding = workspace_binding_for_receipt(
        package.receipt,
        receipt_sha256=package.receipt_sha256,
    )
    request = bind_workspace_request(
        AdapterRequest(
            action=AdapterAction.EVALUATE,
            request_id="evaluate:workspace:candidate-1",
            project="pimc",
            run_id="run-1",
            candidate_id="candidate-1",
        ),
        binding,
    )
    (inputs / "adapter_request.json").write_text(
        request.model_dump_json(),
        encoding="utf-8",
    )
    adapter_script = tmp_path / "must_not_run.py"
    adapter_script.write_text(
        "from pathlib import Path\nPath('adapter-called').write_text('bad')\n",
        encoding="utf-8",
    )

    response = await run_worker(
        request_file="inputs/adapter_request.json",
        output_file="response.json",
        trusted_argv=(sys.executable, str(adapter_script)),
        timeout_seconds=5.0,
        job_root=tmp_path,
    )

    assert response.status == "failed"
    assert response.error_code == "workspace_verification_failed"
    assert not (tmp_path / "adapter-called").exists()
    assert not (tmp_path / "workspace").exists()


def _workspace_package(
    root: Path,
    *,
    replacement_version: int,
) -> CodeWorkspaceTransferPackage:
    root.mkdir(parents=True)
    source = root / "source"
    source.mkdir()
    baseline = b"def build_model(config):\n    return {'version': 1}\n"
    (source / "model.py").write_bytes(baseline)
    snapshot = create_snapshot(
        source_root=source,
        cache_root=root / "snapshots",
        project="pimc",
        source_ref="test-baseline",
        policy=SnapshotPolicy(allowed_paths=("model.py",)),
    )
    replacement = (
        "def build_model(config):\n"
        f"    return {{'version': {replacement_version}}}\n"
    ).encode("utf-8")
    digest = content_sha256(replacement)
    blob_root = root / "blobs"
    blob_path = content_blob_path(blob_root, digest)
    blob_path.parent.mkdir(parents=True)
    blob_path.write_bytes(replacement)
    bundle = CodeMaterializationBundle(
        base_snapshot_id=snapshot.manifest.snapshot_id,
        code_spec_sha256=content_sha256(
            f"code-spec-{replacement_version}".encode("utf-8")
        ),
        operations=(
            CodeBlobOperation(
                path="model.py",
                action="replace",
                content_sha256=digest,
                expected_base_sha256=content_sha256(baseline),
            ),
        ),
    )
    workspace = materialize_code_workspace(
        snapshot_root=snapshot.root,
        blob_root=blob_root,
        workspaces_root=root / "workspaces",
        candidate_id="candidate-1",
        bundle=bundle,
        allowed_paths=("model.py",),
        expected_touched_paths=("model.py",),
        expected_entrypoint="model.py",
    )
    return build_code_workspace_transfer(
        workspace_root=workspace.root,
        snapshot_root=snapshot.root,
        bundle=bundle,
        expected_touched_paths=("model.py",),
        expected_entrypoint="model.py",
        transfer_root=root / "transfer",
    )
