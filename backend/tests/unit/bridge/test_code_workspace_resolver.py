from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

from app.bridge.candidate_workspace import (
    CandidateWorkspaceManager,
    CandidateWorkspaceReceipt,
)
from app.bridge.code_workspace_resolver import (
    PersistedCodeWorkspaceResolver,
    PersistedWorkspaceResolutionError,
)
from app.execution.adapters.base import AdapterAction, AdapterRequest
from app.execution.adapters.process import ProcessAdapter
from app.harness.discovery.candidate_builder import (
    build_candidate_record,
    genome_fingerprint,
)
from app.harness.discovery.code_candidate import (
    CodeCandidateSpec,
    TensorInterfaceSpec,
    code_candidate_implementation_fingerprint,
    code_candidate_spec_sha256,
)
from app.harness.discovery.code_materialization import (
    CodeBlobOperation,
    CodeMaterializationBundle,
    bundle_sha256,
    content_blob_path,
    content_sha256,
)
from app.harness.discovery.models import (
    CandidateRecord,
    CandidateStatus,
    ModelGenome,
    ResearchTaskContract,
)
from app.harness.discovery.preflight import run_code_candidate_preflight
from app.harness.discovery.snapshots import SnapshotPolicy, create_snapshot
from app.harness.tools.project_repo import ProjectRepo
from app.harness.tools.registry import ToolContext, ToolRegistry, ToolResult
from app.storage.discovery_candidate_store import CandidateStore
from app.storage.discovery_common import atomic_write_json
from app.storage.run_store import RunHandle, RunStore


@pytest.mark.asyncio
async def test_resolver_recovers_from_receipt_and_ignores_request_config_paths(
    tmp_path: Path,
) -> None:
    case = await _persisted_case(tmp_path)
    outside = tmp_path / "attacker-workspace"
    outside.mkdir()
    (outside / "model.py").write_text("MALICIOUS = True\n", encoding="utf-8")
    request = _request(
        case,
        config={
            "workspace_ref": str(outside),
            "receipt_ref": "../../attacker-receipt.json",
            "bundle_ref": str(outside / "bundle.json"),
        },
    )

    first = await PersistedCodeWorkspaceResolver(case.runs_root).resolve(request)
    current_record = next(
        (case.parent.root / "discovery" / "candidates" / "current").glob("*.json")
    )
    current_record.unlink()
    restarted = await PersistedCodeWorkspaceResolver(case.runs_root).resolve(request)

    assert first is not None
    assert restarted is not None
    assert first.receipt == restarted.receipt
    assert first.receipt_sha256 == restarted.receipt_sha256
    assert first.archive_path.read_bytes() == restarted.archive_path.read_bytes()
    assert first.receipt.candidate_id == case.candidate.candidate_id
    assert first.archive_path.is_relative_to(case.parent.root)
    assert "MALICIOUS" not in first.archive_path.read_bytes().decode(
        "utf-8",
        errors="ignore",
    )


@pytest.mark.asyncio
async def test_code_candidate_without_receipt_fails_closed(tmp_path: Path) -> None:
    case = await _persisted_case(tmp_path)
    receipt_path = (
        case.parent.root
        / "discovery"
        / "candidate_receipts"
        / f"{case.candidate.candidate_id}.json"
    )
    receipt_path.unlink()

    with pytest.raises(
        PersistedWorkspaceResolutionError,
        match="no persisted workspace receipt",
    ):
        await PersistedCodeWorkspaceResolver(case.runs_root).resolve(_request(case))


@pytest.mark.asyncio
async def test_resolver_rejects_symlinked_workspace_and_unrelated_child_run(
    tmp_path: Path,
) -> None:
    case = await _persisted_case(tmp_path)
    assert case.prepared_receipt.workspace_ref
    workspace = case.parent.root / case.prepared_receipt.workspace_ref
    backup = workspace.with_name(f"{workspace.name}-backup")
    workspace.rename(backup)
    workspace.symlink_to(backup, target_is_directory=True)

    with pytest.raises(PersistedWorkspaceResolutionError, match="symbolic link"):
        await PersistedCodeWorkspaceResolver(case.runs_root).resolve(_request(case))

    workspace.unlink()
    backup.rename(workspace)
    unrelated = RunStore(case.runs_root).create(
        task="unrelated",
        project="pimc",
        now=datetime(2026, 8, 26, 12, 2, tzinfo=timezone.utc),
    )
    with pytest.raises(
        PersistedWorkspaceResolutionError,
        match="not a persisted child",
    ):
        await PersistedCodeWorkspaceResolver(case.runs_root).resolve(
            _request(case, run_id=unrelated.run_id)
        )


@pytest.mark.asyncio
async def test_config_candidate_without_code_artifacts_needs_no_workspace(
    tmp_path: Path,
) -> None:
    runs_root = tmp_path / "runs"
    store = RunStore(runs_root)
    parent = store.create(
        task="config-parent",
        project="pimc",
        now=datetime(2026, 8, 26, 13, 0, tzinfo=timezone.utc),
    )
    child = store.create(
        task=f"discovery_child__{parent.run_id}__0",
        project="pimc",
        now=datetime(2026, 8, 26, 13, 1, tzinfo=timezone.utc),
    )
    candidate = build_candidate_record(
        run_id=parent.run_id,
        genome=ModelGenome(family="config-only"),
        creator="test",
        operator="sample",
    )
    CandidateStore(parent.root, run_id=parent.run_id).put(candidate)
    request = AdapterRequest(
        action=AdapterAction.EVALUATE,
        request_id=f"evaluate:0:{candidate.candidate_id}",
        project="pimc",
        run_id=child.run_id,
        candidate_id=candidate.candidate_id,
    )

    assert await PersistedCodeWorkspaceResolver(runs_root).resolve(request) is None


@pytest.mark.asyncio
async def test_code_candidate_lifecycle_uses_verified_workspace_after_restart(
    tmp_path: Path,
) -> None:
    case = await _persisted_case(tmp_path)
    report = run_code_candidate_preflight(
        candidate=case.candidate,
        contract=ResearchTaskContract(
            run_id=case.parent.run_id,
            project="pimc",
            objective="code workspace lifecycle e2e",
            allowed_paths=("pkg/",),
        ),
        spec=case.code_spec,
        snapshot_root=case.snapshot_root,
        bundle=case.bundle,
        candidate_workspace=case.workspace_root,
        touched_paths=case.code_spec.touched_paths,
        artifact_metadata={"touched_paths": list(case.code_spec.touched_paths)},
    )
    assert report.passed

    output_dir = tmp_path / "adapter-output"
    adapter_script = tmp_path / "trusted_adapter.py"
    adapter_script.write_text(
        "import json, pathlib, sys\n"
        "request = json.load(sys.stdin)\n"
        "binding = request['config']['_mars_code_workspace']\n"
        "source = pathlib.Path(binding['relative_path'], 'pkg/model.py')\n"
        "assert source.is_file()\n"
        "assert 'return config' in source.read_text(encoding='utf-8')\n"
        "output = pathlib.Path(request['output_dir'])\n"
        "assert output.is_absolute()\n"
        "output.mkdir(parents=True, exist_ok=True)\n"
        "with (output / 'actions').open('a', encoding='utf-8') as stream:\n"
        "    stream.write(request['action'] + '\\n')\n"
        "sys.stdout.write(json.dumps({\n"
        "    'protocol': 'adapter.v1',\n"
        "    'request_id': request['request_id'],\n"
        "    'status': 'ok',\n"
        "}))\n",
        encoding="utf-8",
    )

    def request(action: AdapterAction) -> AdapterRequest:
        return AdapterRequest(
            action=action,
            request_id=f"{action.value}:0:{case.candidate.candidate_id}",
            project="pimc",
            run_id=case.child.run_id,
            candidate_id=case.candidate.candidate_id,
            output_dir=str(output_dir),
        )

    adapter = ProcessAdapter(
        name="lifecycle-local",
        argv=(sys.executable, str(adapter_script)),
        timeout_seconds=5.0,
        workspace_resolver=PersistedCodeWorkspaceResolver(case.runs_root),
    )
    preflight = await adapter.invoke(request(AdapterAction.PREFLIGHT))
    assert preflight.status == "ok"

    candidates = CandidateStore(case.parent.root, run_id=case.parent.run_id)
    candidates.transition(
        case.candidate.candidate_id,
        CandidateStatus.VALIDATED,
        expected_status=CandidateStatus.DRAFT,
    )
    candidates.transition(
        case.candidate.candidate_id,
        CandidateStatus.QUEUED,
        expected_status=CandidateStatus.VALIDATED,
    )
    candidates.transition(
        case.candidate.candidate_id,
        CandidateStatus.RUNNING,
        expected_status=CandidateStatus.QUEUED,
    )
    execute = await adapter.invoke(request(AdapterAction.EXECUTE))
    assert execute.status == "ok"

    current_record = next(
        (case.parent.root / "discovery" / "candidates" / "current").glob("*.json")
    )
    current_record.unlink()
    recovered_store = CandidateStore(case.parent.root, run_id=case.parent.run_id)
    recovered_store.recover()
    recovered = recovered_store.get(case.candidate.candidate_id)
    assert recovered is not None
    assert recovered.status == CandidateStatus.QUEUED
    recovered_store.transition(
        case.candidate.candidate_id,
        CandidateStatus.RUNNING,
        expected_status=CandidateStatus.QUEUED,
    )

    restarted_adapter = ProcessAdapter(
        name="lifecycle-local",
        argv=(sys.executable, str(adapter_script)),
        timeout_seconds=5.0,
        workspace_resolver=PersistedCodeWorkspaceResolver(case.runs_root),
    )
    evaluate = await restarted_adapter.invoke(request(AdapterAction.EVALUATE))
    assert evaluate.status == "ok"
    final = recovered_store.transition(
        case.candidate.candidate_id,
        CandidateStatus.EVALUATED,
        expected_status=CandidateStatus.RUNNING,
    )

    assert final.status == CandidateStatus.EVALUATED
    assert (output_dir / "actions").read_text(encoding="utf-8").splitlines() == [
        "preflight",
        "execute",
        "evaluate",
    ]


@dataclass(frozen=True)
class _PersistedCase:
    runs_root: Path
    parent: RunHandle
    child: RunHandle
    candidate: CandidateRecord
    prepared_receipt: CandidateWorkspaceReceipt
    code_spec: CodeCandidateSpec
    bundle: CodeMaterializationBundle
    snapshot_root: Path
    workspace_root: Path


async def _persisted_case(tmp_path: Path) -> _PersistedCase:
    runs_root = tmp_path / "runs"
    store = RunStore(runs_root)
    parent = store.create(
        task="code-parent",
        project="pimc",
        now=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
    )
    child = store.create(
        task=f"discovery_child__{parent.run_id}__0",
        project="pimc",
        now=datetime(2026, 8, 26, 12, 1, tzinfo=timezone.utc),
    )
    source = tmp_path / "live-repo"
    (source / "pkg").mkdir(parents=True)
    (source / "pkg" / "model.py").write_text(
        "def build_model(config):\n    return 1\n",
        encoding="utf-8",
    )
    repo = ProjectRepo(
        project="pimc",
        root=source,
        repo_mode="local_path",
        read_only=False,
        allowed_paths=("pkg/",),
        protected_paths=(),
        ignore_patterns=(),
    )
    snapshot = create_snapshot(
        source_root=repo.root,
        cache_root=parent.root / "discovery" / "source_snapshots",
        project=repo.project,
        source_ref=f"{repo.repo_mode}:{repo.project}",
        policy=SnapshotPolicy(allowed_paths=repo.allowed_paths),
    )
    replacement = b"def build_model(config):\n    return config\n"
    replacement_hash = content_sha256(replacement)
    blob_path = content_blob_path(
        parent.root / "discovery" / "code_blobs",
        replacement_hash,
    )
    blob_path.parent.mkdir(parents=True)
    blob_path.write_bytes(replacement)
    spec = CodeCandidateSpec(
        base_snapshot_id=snapshot.manifest.snapshot_id,
        entrypoint="pkg/model.py",
        patch_ref=blob_path.relative_to(parent.root).as_posix(),
        patch_sha256=replacement_hash,
        touched_paths=("pkg/model.py",),
        interface=TensorInterfaceSpec(
            input_rank=2,
            output_rank=2,
            input_dtype="float32",
            output_dtype="float32",
        ),
    )
    bundle = CodeMaterializationBundle(
        base_snapshot_id=snapshot.manifest.snapshot_id,
        code_spec_sha256=code_candidate_spec_sha256(spec),
        operations=(
            CodeBlobOperation(
                path="pkg/model.py",
                action="replace",
                content_sha256=replacement_hash,
                expected_base_sha256=snapshot.manifest.files[0].sha256,
            ),
        ),
    )
    spec_ref = "coding/code_candidate_spec.json"
    bundle_ref = "coding/code_materialization_bundle.json"
    atomic_write_json(
        parent.root / spec_ref,
        spec.model_dump(mode="json"),
    )
    atomic_write_json(
        parent.root / bundle_ref,
        bundle.model_dump(mode="json"),
    )
    genome = ModelGenome(family="secure-test", mutable_zones=("structure",))
    implementation = code_candidate_implementation_fingerprint(
        genome_exact_sha256=genome_fingerprint(genome),
        bundle_hash=bundle_sha256(bundle),
    )
    candidate = build_candidate_record(
        run_id=parent.run_id,
        genome=genome,
        creator="coding",
        operator="materialize",
        implementation_fingerprint=implementation,
        artifact_refs={
            "code_candidate_spec": spec_ref,
            "code_materialization_bundle": bundle_ref,
        },
    )
    CandidateStore(parent.root, run_id=parent.run_id).put(candidate)
    prepared = await CandidateWorkspaceManager().prepare_secure_from_repo(
        repo=repo,
        run_root=parent.root,
        candidate=candidate,
        code_spec=spec,
        bundle=bundle,
        tool_registry=_allowing_registry(),
    )
    assert prepared.receipt is not None
    return _PersistedCase(
        runs_root=runs_root,
        parent=parent,
        child=child,
        candidate=candidate,
        prepared_receipt=prepared.receipt,
        code_spec=spec,
        bundle=bundle,
        snapshot_root=prepared.snapshot.root,
        workspace_root=prepared.root,
    )


def _request(
    case: _PersistedCase,
    *,
    run_id: str | None = None,
    config: dict[str, object] | None = None,
) -> AdapterRequest:
    return AdapterRequest(
        action=AdapterAction.EVALUATE,
        request_id=f"evaluate:0:{case.candidate.candidate_id}",
        project="pimc",
        run_id=run_id or case.child.run_id,
        candidate_id=case.candidate.candidate_id,
        config=dict(config or {}),
    )


def _allowing_registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def allow_tool(_args: dict[str, object], _context: ToolContext) -> ToolResult:
        return ToolResult(ok=True, output={"diff": "", "files": []})

    registry.register("code.patch_generator", allow_tool)
    return registry
