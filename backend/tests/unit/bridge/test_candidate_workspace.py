from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import app.bridge.candidate_workspace as candidate_workspace_module
from app.bridge.candidate_workspace import (
    CandidateWorkspaceError,
    CandidateWorkspaceManager,
    SecureCandidateWorkspacePreparer,
)
from app.harness.discovery.candidate_builder import build_candidate_record, genome_fingerprint
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
    ModelGenome,
    ResearchTaskContract,
)
from app.harness.discovery.snapshots import (
    SnapshotHandle,
    SnapshotPolicy,
    create_snapshot,
)
from app.harness.tools.project_repo import ProjectRepo
from app.harness.tools.registry import (
    GateDecision,
    ToolContext,
    ToolRegistry,
    ToolResult,
)
from app.storage.run_store import RunHandle


def test_candidate_workspace_manager_never_edits_live_repo(tmp_path: Path) -> None:
    source = tmp_path / "live-repo"
    (source / "libs").mkdir(parents=True)
    live_model = source / "libs" / "model.py"
    live_model.write_text("VALUE = 1\n", encoding="utf-8")
    (source / "data").mkdir()
    (source / "data" / "private.npy").write_bytes(b"private")
    run_root = tmp_path / "run"
    run_root.mkdir()
    repo = ProjectRepo(
        project="pimc",
        root=source,
        repo_mode="local_path",
        read_only=False,
        allowed_paths=("libs/",),
        protected_paths=("libs/model.py:Baseline",),
        ignore_patterns=("data/",),
    )

    prepared = CandidateWorkspaceManager().prepare_from_repo(
        repo=repo,
        run_root=run_root,
        candidate_id="cand_test",
    )
    (prepared.root / "libs/model.py").write_text("VALUE = 2\n", encoding="utf-8")

    assert live_model.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (prepared.root / "data").exists()
    assert prepared.snapshot_ref.startswith("discovery/source_snapshots/snap_")
    assert prepared.workspace_ref == "discovery/candidate_workspaces/cand_test"
    _make_writable(prepared.snapshot.root)


@pytest.mark.asyncio
async def test_secure_prepare_gate_allows_without_editing_live_repo_and_replays_receipt(
    tmp_path: Path,
) -> None:
    case = _secure_case(tmp_path)
    calls: list[tuple[dict[str, Any], ToolContext]] = []
    registry = _allowing_registry(calls)

    first = await CandidateWorkspaceManager().prepare_secure_from_repo(
        repo=case.repo,
        run_root=case.run_root,
        candidate=case.candidate,
        code_spec=case.code_spec,
        bundle=case.bundle,
        tool_registry=registry,
    )
    second = await CandidateWorkspaceManager().prepare_secure_from_repo(
        repo=case.repo,
        run_root=case.run_root,
        candidate=case.candidate,
        code_spec=case.code_spec,
        bundle=case.bundle,
        tool_registry=registry,
    )

    assert case.live_file.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (first.root / "pkg/model.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert first.root == second.root
    assert first.receipt_ref == second.receipt_ref
    assert first.receipt_sha256 == second.receipt_sha256
    assert first.receipt is not None
    assert first.receipt.gate_audit[0].path == "pkg/model.py"
    assert first.receipt.bundle_sha256 == bundle_sha256(case.bundle)
    assert first.receipt.workspace_manifest_sha256 == first.workspace_manifest_sha256
    assert first.receipt_ref.startswith("discovery/candidate_receipts/")
    receipt_path = case.run_root / first.receipt_ref
    assert receipt_path.is_file()
    assert first.root not in receipt_path.parents
    assert len(calls) == 2
    assert all(context.agent == "coding" and context.dry_run for _args, context in calls)
    assert calls[0][0] == {"path": "pkg/model.py", "content": "VALUE = 2\n"}
    _make_writable(case.snapshot.root)


@pytest.mark.asyncio
async def test_secure_prepare_gate_block_and_protected_path_never_publish(
    tmp_path: Path,
) -> None:
    blocked_case = _secure_case(tmp_path / "gate-block")
    blocked_registry = _allowing_registry([])

    async def block_gate(
        tool_name: str,
        args: dict[str, Any],
        context: ToolContext,
    ) -> GateDecision:
        return GateDecision(gate_id="baseline_compatibility", action="block", reason="blocked")

    blocked_registry.install_gate(block_gate)
    with pytest.raises(CandidateWorkspaceError, match="Gate 5 audit rejected"):
        await CandidateWorkspaceManager().prepare_secure_from_repo(
            repo=blocked_case.repo,
            run_root=blocked_case.run_root,
            candidate=blocked_case.candidate,
            code_spec=blocked_case.code_spec,
            bundle=blocked_case.bundle,
            tool_registry=blocked_registry,
        )
    assert not (blocked_case.run_root / "discovery/candidate_workspaces").exists()
    assert not (blocked_case.run_root / "discovery/candidate_receipts").exists()

    protected_case = _secure_case(
        tmp_path / "protected",
        protected_paths=("pkg/model.py:Baseline",),
    )
    with pytest.raises(CandidateWorkspaceError, match="operation path is protected"):
        await CandidateWorkspaceManager().prepare_secure_from_repo(
            repo=protected_case.repo,
            run_root=protected_case.run_root,
            candidate=protected_case.candidate,
            code_spec=protected_case.code_spec,
            bundle=protected_case.bundle,
            tool_registry=_allowing_registry([]),
        )
    assert not (protected_case.run_root / "discovery/candidate_workspaces").exists()
    assert not (protected_case.run_root / "discovery/candidate_receipts").exists()
    _make_writable(blocked_case.snapshot.root)
    _make_writable(protected_case.snapshot.root)


@pytest.mark.asyncio
async def test_secure_prepare_rejects_identity_workspace_and_receipt_tampering(
    tmp_path: Path,
) -> None:
    identity_case = _secure_case(tmp_path / "identity")
    forged = identity_case.candidate.model_copy(
        update={
            "fingerprints": {
                **identity_case.candidate.fingerprints,
                "implementation": "sha256:" + "0" * 64,
            }
        }
    )
    with pytest.raises(CandidateWorkspaceError, match="implementation fingerprint mismatch"):
        await CandidateWorkspaceManager().prepare_secure_from_repo(
            repo=identity_case.repo,
            run_root=identity_case.run_root,
            candidate=forged,
            code_spec=identity_case.code_spec,
            bundle=identity_case.bundle,
            tool_registry=_allowing_registry([]),
        )
    assert not (identity_case.run_root / "discovery/candidate_workspaces").exists()

    tamper_case = _secure_case(tmp_path / "tamper")
    manager = CandidateWorkspaceManager()
    prepared = await manager.prepare_secure_from_repo(
        repo=tamper_case.repo,
        run_root=tamper_case.run_root,
        candidate=tamper_case.candidate,
        code_spec=tamper_case.code_spec,
        bundle=tamper_case.bundle,
        tool_registry=_allowing_registry([]),
    )
    (prepared.root / "pkg/model.py").write_text("VALUE = 999\n", encoding="utf-8")
    with pytest.raises(CandidateWorkspaceError, match="workspace file mismatch"):
        await manager.prepare_secure_from_repo(
            repo=tamper_case.repo,
            run_root=tamper_case.run_root,
            candidate=tamper_case.candidate,
            code_spec=tamper_case.code_spec,
            bundle=tamper_case.bundle,
            tool_registry=_allowing_registry([]),
        )

    (prepared.root / "pkg/model.py").write_text("VALUE = 2\n", encoding="utf-8")
    receipt_path = tamper_case.run_root / prepared.receipt_ref
    receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt_payload["policy_hash"] = "sha256:" + "f" * 64
    receipt_path.write_text(json.dumps(receipt_payload), encoding="utf-8")
    with pytest.raises(CandidateWorkspaceError, match="receipt does not match replay"):
        await manager.prepare_secure_from_repo(
            repo=tamper_case.repo,
            run_root=tamper_case.run_root,
            candidate=tamper_case.candidate,
            code_spec=tamper_case.code_spec,
            bundle=tamper_case.bundle,
            tool_registry=_allowing_registry([]),
        )
    _make_writable(identity_case.snapshot.root)
    _make_writable(tamper_case.snapshot.root)


@pytest.mark.asyncio
async def test_secure_preparer_runs_strict_preflight_before_returning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _secure_case(
        tmp_path,
        source_text="def build_model(config):\n    return 1\n",
        replacement_text="def build_model(config):\n    return config\n",
    )
    candidate = case.candidate.model_copy(
        update={"metadata": {"touched_paths": list(case.code_spec.touched_paths)}}
    )
    _install_case_repo(monkeypatch, case.repo)

    prepared = await SecureCandidateWorkspacePreparer(
        tool_registry=_allowing_registry([]),
    ).prepare(
        run=_run_handle(case),
        contract=_contract(case),
        candidate=candidate,
        code_spec=case.code_spec,
        bundle=case.bundle,
    )

    assert prepared.receipt is not None
    assert (prepared.root / case.code_spec.entrypoint).read_text(encoding="utf-8") == (
        "def build_model(config):\n    return config\n"
    )
    _make_writable(case.snapshot.root)


@pytest.mark.asyncio
async def test_secure_preparer_fails_closed_on_ast_preflight_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _secure_case(tmp_path)
    candidate = case.candidate.model_copy(
        update={"metadata": {"touched_paths": list(case.code_spec.touched_paths)}}
    )
    _install_case_repo(monkeypatch, case.repo)

    with pytest.raises(
        CandidateWorkspaceError,
        match="code_candidate:factory",
    ):
        await SecureCandidateWorkspacePreparer(
            tool_registry=_allowing_registry([]),
        ).prepare(
            run=_run_handle(case),
            contract=_contract(case),
            candidate=candidate,
            code_spec=case.code_spec,
            bundle=case.bundle,
        )

    assert case.live_file.read_text(encoding="utf-8") == "VALUE = 1\n"
    _make_writable(case.snapshot.root)


@pytest.mark.asyncio
async def test_secure_preparer_rejects_contract_repo_policy_drift_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _secure_case(
        tmp_path,
        protected_paths=("pkg/model.py:Baseline",),
    )
    _install_case_repo(monkeypatch, case.repo)
    preparer = SecureCandidateWorkspacePreparer(
        tool_registry=_allowing_registry([]),
    )

    with pytest.raises(CandidateWorkspaceError, match="expands repository policy"):
        await preparer.prepare(
            run=_run_handle(case),
            contract=_contract(case, allowed_paths=("outside/",)),
            candidate=case.candidate,
            code_spec=case.code_spec,
            bundle=case.bundle,
        )
    with pytest.raises(CandidateWorkspaceError, match="do not cover repository protected"):
        await preparer.prepare(
            run=_run_handle(case),
            contract=_contract(case),
            candidate=case.candidate,
            code_spec=case.code_spec,
            bundle=case.bundle,
        )

    assert not (case.run_root / "discovery/candidate_workspaces").exists()
    _make_writable(case.snapshot.root)


def test_main_composes_secure_candidate_workspace_preparer() -> None:
    from app.main import create_app

    application = create_app()

    assert isinstance(
        application.state.discovery_service.code_candidate_preparer,
        SecureCandidateWorkspacePreparer,
    )


@dataclass(frozen=True)
class _SecureCase:
    repo: ProjectRepo
    run_root: Path
    candidate: CandidateRecord
    code_spec: CodeCandidateSpec
    bundle: CodeMaterializationBundle
    snapshot: SnapshotHandle
    live_file: Path


def _secure_case(
    root: Path,
    *,
    protected_paths: tuple[str, ...] = (),
    source_text: str = "VALUE = 1\n",
    replacement_text: str = "VALUE = 2\n",
) -> _SecureCase:
    source = root / "live-repo"
    (source / "pkg").mkdir(parents=True)
    live_file = source / "pkg/model.py"
    live_file.write_text(source_text, encoding="utf-8")
    run_root = root / "run"
    run_root.mkdir()
    repo = ProjectRepo(
        project="pimc",
        root=source,
        repo_mode="local_path",
        read_only=False,
        allowed_paths=("pkg/",),
        protected_paths=protected_paths,
        ignore_patterns=(),
    )
    snapshot = create_snapshot(
        source_root=repo.root,
        cache_root=run_root / "discovery/source_snapshots",
        project=repo.project,
        source_ref=f"{repo.repo_mode}:{repo.project}",
        policy=SnapshotPolicy(allowed_paths=repo.allowed_paths),
    )
    replacement = replacement_text.encode("utf-8")
    replacement_hash = content_sha256(replacement)
    blob_path = content_blob_path(run_root / "discovery/code_blobs", replacement_hash)
    blob_path.parent.mkdir(parents=True)
    blob_path.write_bytes(replacement)
    base_hash = snapshot.manifest.files[0].sha256
    preliminary_spec = CodeCandidateSpec(
        base_snapshot_id=snapshot.manifest.snapshot_id,
        entrypoint="pkg/model.py",
        patch_ref=blob_path.relative_to(run_root).as_posix(),
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
        code_spec_sha256=code_candidate_spec_sha256(preliminary_spec),
        operations=(
            CodeBlobOperation(
                path="pkg/model.py",
                action="replace",
                content_sha256=replacement_hash,
                expected_base_sha256=base_hash,
            ),
        ),
    )
    genome = ModelGenome(family="secure-test", mutable_zones=("structure",))
    implementation = code_candidate_implementation_fingerprint(
        genome_exact_sha256=genome_fingerprint(genome),
        bundle_hash=bundle_sha256(bundle),
    )
    candidate = build_candidate_record(
        run_id="run-secure",
        genome=genome,
        creator="coding",
        operator="materialize",
        implementation_fingerprint=implementation,
    )
    return _SecureCase(
        repo=repo,
        run_root=run_root,
        candidate=candidate,
        code_spec=preliminary_spec,
        bundle=bundle,
        snapshot=snapshot,
        live_file=live_file,
    )


def _run_handle(case: _SecureCase) -> RunHandle:
    return RunHandle(
        run_id=case.candidate.run_id,
        root=case.run_root,
        project=case.repo.project,
        task="secure-code-test",
        entrypoint="model_discovery",
        created_at="2026-08-26T00:00:00+00:00",
    )


def _contract(
    case: _SecureCase,
    *,
    allowed_paths: tuple[str, ...] = ("pkg/",),
    forbidden_paths: tuple[str, ...] = (),
) -> ResearchTaskContract:
    return ResearchTaskContract(
        run_id=case.candidate.run_id,
        project=case.repo.project,
        objective="validate secure code candidate",
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
    )


def _install_case_repo(
    monkeypatch: pytest.MonkeyPatch,
    repo: ProjectRepo,
) -> None:
    def load_repo(project: str) -> ProjectRepo:
        assert project == repo.project
        return repo

    monkeypatch.setattr(candidate_workspace_module, "load_project_repo", load_repo)


def _allowing_registry(
    calls: list[tuple[dict[str, Any], ToolContext]],
) -> ToolRegistry:
    registry = ToolRegistry()

    async def allow_tool(args: dict[str, Any], context: ToolContext) -> ToolResult:
        calls.append((args, context))
        return ToolResult(ok=True, output={"diff": "", "files": []})

    registry.register("code.patch_generator", allow_tool)
    return registry


def _make_writable(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif not path.is_symlink():
            path.chmod(0o644)
    root.chmod(0o755)
