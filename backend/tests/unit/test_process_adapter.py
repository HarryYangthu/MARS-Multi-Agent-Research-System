from __future__ import annotations

from pathlib import Path
import sys

import pytest

from app.execution.adapters.base import AdapterAction, AdapterRequest
from app.execution.adapters.process import ProcessAdapter
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


class _StaticWorkspaceResolver:
    def __init__(self, package: CodeWorkspaceTransferPackage | None) -> None:
        self.package = package
        self.calls = 0

    async def resolve(
        self,
        _request: AdapterRequest,
    ) -> CodeWorkspaceTransferPackage | None:
        self.calls += 1
        return self.package


@pytest.mark.asyncio
async def test_process_adapter_runs_at_private_job_root_with_verified_binding(
    tmp_path: Path,
) -> None:
    package = _workspace_package(
        tmp_path / "package",
        entrypoint="trusted_adapter.py",
        replacement=b"raise RuntimeError('candidate module must not be imported')\n",
    )
    trusted_src = tmp_path / "trusted-src"
    trusted_src.mkdir()
    output_dir = tmp_path / "outputs"
    (trusted_src / "trusted_adapter.py").write_text(
        "import hashlib, json, pathlib, sys\n"
        "request = json.load(sys.stdin)\n"
        "binding = request['config']['_mars_code_workspace']\n"
        "candidate = pathlib.Path(binding['relative_path'], 'trusted_adapter.py')\n"
        "assert candidate.is_file()\n"
        "assert 'candidate module' in candidate.read_text(encoding='utf-8')\n"
        "archive = pathlib.Path('inputs/code_workspace.tar')\n"
        "receipt = pathlib.Path('inputs/code_workspace_receipt.json')\n"
        "assert archive.is_file() and receipt.is_file()\n"
        "actual_receipt_hash = 'sha256:' + hashlib.sha256(receipt.read_bytes()).hexdigest()\n"
        "assert actual_receipt_hash == binding['receipt_sha256']\n"
        "output = pathlib.Path(request['output_dir'])\n"
        "assert output.is_absolute()\n"
        "output.mkdir(parents=True, exist_ok=True)\n"
        "(output / 'adapter-called').write_text('trusted', encoding='utf-8')\n"
        "sys.stdout.write(json.dumps({\n"
        "    'protocol': 'adapter.v1',\n"
        "    'request_id': request['request_id'],\n"
        "    'status': 'ok',\n"
        "    'raw_metrics': {\n"
        "        'adapter_source': 'trusted',\n"
        "        'cwd_contains_workspace': pathlib.Path('workspace').is_dir(),\n"
        "    },\n"
        "}))\n",
        encoding="utf-8",
    )
    resolver = _StaticWorkspaceResolver(package)
    adapter = ProcessAdapter(
        name="trusted-local",
        argv=(sys.executable, "-m", "trusted_adapter"),
        timeout_seconds=5.0,
        env={
            "PYTHONPATH": str(trusted_src),
            # ProcessAdapter must override an unsafe composition value.
            "PYTHONSAFEPATH": "0",
        },
        workspace_resolver=resolver,
    )
    request = _request(output_dir=output_dir)

    response = await adapter.invoke(request)

    assert response.status == "ok"
    assert response.raw_metrics == {
        "adapter_source": "trusted",
        "cwd_contains_workspace": True,
    }
    assert (output_dir / "adapter-called").read_text(encoding="utf-8") == "trusted"
    assert resolver.calls == 1


@pytest.mark.asyncio
async def test_process_adapter_strips_control_plane_secrets_from_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ambient-deepseek-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-github-secret")
    monkeypatch.setenv("MARS_REMOTE_SSH_HOST", "private-control-host")
    script = tmp_path / "inspect_environment.py"
    script.write_text(
        "import json, os, sys\n"
        "request = json.load(sys.stdin)\n"
        "names = ('DEEPSEEK_API_KEY', 'GITHUB_TOKEN', 'MARS_REMOTE_SSH_HOST', "
        "'PACK_VISIBLE')\n"
        "sys.stdout.write(json.dumps({\n"
        "    'protocol': 'adapter.v1',\n"
        "    'request_id': request['request_id'],\n"
        "    'status': 'ok',\n"
        "    'raw_metrics': {name: os.environ.get(name, '') for name in names},\n"
        "}))\n",
        encoding="utf-8",
    )
    adapter = ProcessAdapter(
        name="environment-boundary",
        argv=(sys.executable, str(script)),
        timeout_seconds=5.0,
        env={
            "PACK_VISIBLE": "yes",
            "EXPLICIT_API_KEY": "must-also-be-removed",
        },
    )

    response = await adapter.invoke(_request(output_dir=tmp_path / "outputs"))

    assert response.status == "ok"
    assert response.raw_metrics == {
        "DEEPSEEK_API_KEY": "",
        "GITHUB_TOKEN": "",
        "MARS_REMOTE_SSH_HOST": "",
        "PACK_VISIBLE": "yes",
    }


@pytest.mark.asyncio
async def test_process_adapter_tampering_fails_before_process_launch(
    tmp_path: Path,
) -> None:
    package = _workspace_package(tmp_path / "package")
    payload = bytearray(package.archive_path.read_bytes())
    payload[512] ^= 1
    package.archive_path.write_bytes(payload)
    marker = tmp_path / "process-started"
    script = tmp_path / "must_not_run.py"
    script.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('bad', encoding='utf-8')\n",
        encoding="utf-8",
    )
    adapter = ProcessAdapter(
        name="trusted-local",
        argv=(sys.executable, str(script)),
        timeout_seconds=5.0,
        workspace_resolver=_StaticWorkspaceResolver(package),
    )

    response = await adapter.invoke(_request(output_dir=tmp_path / "outputs"))

    assert response.status == "failed"
    assert response.error_code == "code_workspace_resolution_failed"
    assert "archive hash mismatch" in response.error
    assert not marker.exists()


@pytest.mark.asyncio
async def test_process_adapter_required_workspace_never_falls_back_to_process(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "process-started"
    script = tmp_path / "must_not_run.py"
    script.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('bad', encoding='utf-8')\n",
        encoding="utf-8",
    )
    request = _request(output_dir=tmp_path / "outputs")

    without_resolver = ProcessAdapter(
        name="required-local",
        argv=(sys.executable, str(script)),
        workspace_required=True,
    )
    missing_package = ProcessAdapter(
        name="required-local",
        argv=(sys.executable, str(script)),
        workspace_resolver=_StaticWorkspaceResolver(None),
        workspace_required=True,
    )

    no_resolver_response = await without_resolver.invoke(request)
    no_package_response = await missing_package.invoke(request)

    assert no_resolver_response.error_code == "code_workspace_resolver_required"
    assert no_package_response.error_code == "code_workspace_resolution_failed"
    assert not marker.exists()


@pytest.mark.asyncio
async def test_process_adapter_config_only_none_preserves_legacy_path(
    tmp_path: Path,
) -> None:
    script = tmp_path / "config_adapter.py"
    script.write_text(
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "sys.stdout.write(json.dumps({\n"
        "    'protocol': 'adapter.v1',\n"
        "    'request_id': request['request_id'],\n"
        "    'status': 'ok',\n"
        "}))\n",
        encoding="utf-8",
    )
    resolver = _StaticWorkspaceResolver(None)
    adapter = ProcessAdapter(
        name="config-local",
        argv=(sys.executable, str(script)),
        timeout_seconds=5.0,
        workspace_resolver=resolver,
    )

    response = await adapter.invoke(_request(output_dir=tmp_path / "outputs"))

    assert response.status == "ok"
    assert resolver.calls == 1


def _request(*, output_dir: Path) -> AdapterRequest:
    return AdapterRequest(
        action=AdapterAction.EVALUATE,
        request_id="evaluate:workspace:candidate-1",
        project="pimc",
        run_id="run-1",
        candidate_id="candidate-1",
        output_dir=str(output_dir),
    )


def _workspace_package(
    root: Path,
    *,
    entrypoint: str = "model.py",
    replacement: bytes | None = None,
) -> CodeWorkspaceTransferPackage:
    root.mkdir(parents=True)
    source = root / "source"
    source.mkdir()
    baseline = b"VALUE = 'baseline'\n"
    (source / entrypoint).write_bytes(baseline)
    snapshot = create_snapshot(
        source_root=source,
        cache_root=root / "snapshots",
        project="pimc",
        source_ref="test-baseline",
        policy=SnapshotPolicy(allowed_paths=(entrypoint,)),
    )
    candidate_source = replacement or b"VALUE = 'candidate'\n"
    digest = content_sha256(candidate_source)
    blob_root = root / "blobs"
    blob_path = content_blob_path(blob_root, digest)
    blob_path.parent.mkdir(parents=True)
    blob_path.write_bytes(candidate_source)
    bundle = CodeMaterializationBundle(
        base_snapshot_id=snapshot.manifest.snapshot_id,
        code_spec_sha256=content_sha256(b"code-spec"),
        operations=(
            CodeBlobOperation(
                path=entrypoint,
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
        allowed_paths=(entrypoint,),
        expected_touched_paths=(entrypoint,),
        expected_entrypoint=entrypoint,
    )
    return build_code_workspace_transfer(
        workspace_root=workspace.root,
        snapshot_root=snapshot.root,
        bundle=bundle,
        expected_touched_paths=(entrypoint,),
        expected_entrypoint=entrypoint,
        transfer_root=root / "transfer",
    )
