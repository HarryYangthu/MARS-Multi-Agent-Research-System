"""Actual worker filesystem/environment checks and pure request binding; no fabricated remote jobs."""
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
from app.execution.remote.adapter import RemoteProjectAdapter, _adapter_request_file, _canonical_request, _remote_request_id
from app.execution.remote.adapter_worker import run_worker
from app.execution.remote.executor import RemoteExecutor, RemoteExecutorConfig
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
        "    'raw_metrics': {},\n"
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


def test_remote_adapter_serializes_actual_request_file_and_keeps_payload_out_of_argv(tmp_path: Path) -> None:
    adapter = RemoteProjectAdapter(
        name="remote-contract", client=RemoteExecutor(RemoteExecutorConfig()),
        trusted_adapter_argv=("/opt/mars/bin/python", "-m", "approved_adapter"), artifact_root=tmp_path,
    )
    request = AdapterRequest(action=AdapterAction.EVALUATE, request_id="evaluate:0:candidate",
                             project="pimc", run_id="run/unsafe", candidate_id="candidate/unsafe",
                             config={"authored_input": "must-not-enter-argv"})
    with _adapter_request_file(request) as path:
        parsed = AdapterRequest.model_validate_json(path.read_text())
        assert parsed == request
    assert not path.exists()
    assert "must-not-enter-argv" not in " ".join(adapter._workload_argv())
    identity = _remote_request_id(_canonical_request(request))
    assert identity.startswith("adapter-") and ":" not in identity and "/" not in identity


@pytest.mark.asyncio
async def test_unconfigured_real_executor_cannot_return_remote_metrics(tmp_path: Path) -> None:
    adapter = RemoteProjectAdapter(
        name="unconfigured", client=RemoteExecutor(RemoteExecutorConfig()),
        trusted_adapter_argv=("python3", "-m", "approved_adapter"), artifact_root=tmp_path,
    )
    request = AdapterRequest(action=AdapterAction.EVALUATE, request_id="evaluate:1",
                             project="pimc", run_id="run", candidate_id="candidate")
    response = await adapter.invoke(request)
    assert response.status == "failed" and response.raw_metrics == {}
    assert response.error_code == "remote_adapter_transport_failed"


def test_remote_project_adapter_rejects_shell_as_inner_adapter(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="shell interpreters"):
        RemoteProjectAdapter(name="unsafe", client=RemoteExecutor(RemoteExecutorConfig()),
                             trusted_adapter_argv=("bash", "adapter.sh"), artifact_root=tmp_path)


def test_actual_workspace_bytes_determine_bound_request_identity(tmp_path: Path) -> None:
    first = _workspace_package(tmp_path / "first", replacement_version=2)
    second = _workspace_package(tmp_path / "second", replacement_version=3)
    request = AdapterRequest(action=AdapterAction.EVALUATE, request_id="evaluate:workspace:candidate-1",
                             project="pimc", run_id="run-1", candidate_id="candidate-1")
    identities = []
    for package in (first, second):
        binding = workspace_binding_for_receipt(package.receipt, receipt_sha256=package.receipt_sha256)
        bound = bind_workspace_request(request, binding)
        restored = workspace_binding_from_request(bound)
        assert restored is not None and restored.archive_sha256 == package.receipt.archive_sha256
        identities.append(_remote_request_id(_canonical_request(bound)))
    assert identities[0] != identities[1]


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
