from __future__ import annotations

import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

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
    MaterializedCodeWorkspace,
    bundle_sha256,
    content_blob_path,
    content_sha256,
    materialize_code_workspace,
)
from app.harness.discovery.models import (
    CandidateRecord,
    ModelGenome,
    ResearchTaskContract,
)
from app.harness.discovery.preflight import PreflightReport, run_code_candidate_preflight
from app.harness.discovery.snapshots import (
    SnapshotHandle,
    SnapshotPolicy,
    create_snapshot,
)


@dataclass(frozen=True)
class _Case:
    snapshot: SnapshotHandle
    blob_root: Path
    workspaces_root: Path
    candidate: CandidateRecord
    contract: ResearchTaskContract
    spec: CodeCandidateSpec
    bundle: CodeMaterializationBundle
    workspace: MaterializedCodeWorkspace


_DEFAULT_WORKSPACE = object()


@pytest.fixture
def code_case(tmp_path: Path) -> Iterator[_Case]:
    source = tmp_path / "source"
    entrypoint = source / "candidate" / "model.py"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text(
        "def build_model(config):\n    return config['baseline']\n",
        encoding="utf-8",
    )
    snapshot = create_snapshot(
        source_root=source,
        cache_root=tmp_path / "snapshots",
        project="pimc",
        source_ref="test",
        policy=SnapshotPolicy(allowed_paths=("candidate/",)),
    )
    contract = ResearchTaskContract(
        run_id="run-code-preflight",
        project="pimc",
        objective="improve residual",
        allowed_paths=("candidate/",),
        forbidden_paths=("baseline/",),
    )
    candidate_source = b"def build_model(config):\n    return config['model']\n"
    blob_root = tmp_path / "blobs"
    source_hash = _store_blob(blob_root, candidate_source)
    spec = CodeCandidateSpec(
        base_snapshot_id=snapshot.manifest.snapshot_id,
        entrypoint="candidate/model.py",
        factory="build_model",
        patch_ref="artifact://coding/candidate-source.py",
        patch_sha256=source_hash,
        touched_paths=("candidate/model.py",),
        interface=TensorInterfaceSpec(
            input_rank=2,
            output_rank=2,
            input_dtype="complex64",
            output_dtype="complex64",
        ),
    )
    bundle = CodeMaterializationBundle(
        base_snapshot_id=snapshot.manifest.snapshot_id,
        code_spec_sha256=code_candidate_spec_sha256(spec),
        operations=(
            CodeBlobOperation(
                path=spec.entrypoint,
                action="replace",
                content_sha256=source_hash,
                expected_base_sha256=_snapshot_hash(snapshot, spec.entrypoint),
            ),
        ),
    )
    genome = ModelGenome(family="static_pimc")
    implementation = code_candidate_implementation_fingerprint(
        genome_exact_sha256=genome_fingerprint(genome),
        bundle_hash=bundle_sha256(bundle),
    )
    candidate = build_candidate_record(
        run_id=contract.run_id,
        genome=genome,
        creator="coding",
        operator="additive_block",
        implementation_fingerprint=implementation,
        metadata={"touched_paths": list(spec.touched_paths)},
    )
    workspaces_root = tmp_path / "workspaces"
    workspace = _materialize(
        snapshot=snapshot,
        blob_root=blob_root,
        workspaces_root=workspaces_root,
        candidate=candidate,
        contract=contract,
        spec=spec,
        bundle=bundle,
    )
    yield _Case(
        snapshot=snapshot,
        blob_root=blob_root,
        workspaces_root=workspaces_root,
        candidate=candidate,
        contract=contract,
        spec=spec,
        bundle=bundle,
        workspace=workspace,
    )
    _make_writable(snapshot.root)


def test_valid_materialized_code_candidate_passes_strict_preflight(
    code_case: _Case,
) -> None:
    report = _run(code_case)

    assert report.passed
    assert not report.blockers
    assert {
        "code_snapshot_integrity",
        "code_snapshot_identity",
        "code_bundle_snapshot",
        "code_bundle_spec",
        "code_bundle_paths",
        "code_bundle_entrypoint",
        "code_implementation_fingerprint",
        "code_workspace_required",
        "code_workspace_provenance",
        "code_entrypoint_source",
        "code_candidate:factory",
    }.issubset({check.check_id for check in report.checks})


def test_missing_workspace_never_falls_back_to_baseline_snapshot(
    code_case: _Case,
) -> None:
    report = _run(code_case, candidate_workspace=None)

    assert not report.passed
    assert {check.check_id for check in report.blockers} == {
        "code_workspace_required"
    }
    assert "code_entrypoint_source" not in {check.check_id for check in report.checks}


def test_tampered_workspace_fails_provenance_before_ast(code_case: _Case) -> None:
    entrypoint = code_case.workspace.root / code_case.spec.entrypoint
    entrypoint.write_text(
        "def build_model(config):\n    return config['tampered']\n",
        encoding="utf-8",
    )

    report = _run(code_case)

    assert not report.passed
    blocker = next(
        check
        for check in report.blockers
        if check.check_id == "code_workspace_provenance"
    )
    assert "workspace file mismatch" in blocker.reason
    assert "code_candidate:factory" not in {check.check_id for check in report.checks}


def test_workspace_forged_for_another_candidate_is_rejected(
    code_case: _Case,
    tmp_path: Path,
) -> None:
    forged_candidate = build_candidate_record(
        run_id=code_case.contract.run_id,
        genome=code_case.candidate.genome,
        creator=code_case.candidate.creator,
        operator="different_operator",
        implementation_fingerprint=code_case.candidate.fingerprints["implementation"],
        metadata={"touched_paths": list(code_case.spec.touched_paths)},
    )
    forged_workspace = _materialize(
        snapshot=code_case.snapshot,
        blob_root=code_case.blob_root,
        workspaces_root=tmp_path / "forged-workspaces",
        candidate=forged_candidate,
        contract=code_case.contract,
        spec=code_case.spec,
        bundle=code_case.bundle,
    )

    report = _run(code_case, candidate_workspace=forged_workspace.root)

    assert not report.passed
    blocker = next(
        check
        for check in report.blockers
        if check.check_id == "code_workspace_provenance"
    )
    assert "candidate_id" in blocker.reason


@pytest.mark.parametrize(
    ("bundle_update", "expected_blocker"),
    [
        ({"base_snapshot_id": "snap_" + "f" * 24}, "code_bundle_snapshot"),
        ({"code_spec_sha256": "sha256:" + "f" * 64}, "code_bundle_spec"),
    ],
)
def test_bundle_identity_drift_is_rejected(
    code_case: _Case,
    bundle_update: dict[str, object],
    expected_blocker: str,
) -> None:
    drifted = code_case.bundle.model_copy(update=bundle_update)

    report = _run(code_case, bundle=drifted)

    assert not report.passed
    assert expected_blocker in {check.check_id for check in report.blockers}
    assert "code_workspace_provenance" not in {
        check.check_id for check in report.checks
    }


def test_canonical_spec_drift_is_rejected(code_case: _Case) -> None:
    drifted_spec = code_case.spec.model_copy(
        update={"patch_ref": "artifact://coding/another-source.py"}
    )

    report = _run(code_case, spec=drifted_spec)

    assert not report.passed
    assert "code_bundle_spec" in {check.check_id for check in report.blockers}


def test_bundle_operation_paths_and_entrypoint_must_match_spec(
    code_case: _Case,
) -> None:
    drifted_operation = code_case.bundle.operations[0].model_copy(
        update={"path": "candidate/other.py", "action": "add", "expected_base_sha256": None}
    )
    drifted_bundle = code_case.bundle.model_copy(
        update={"operations": (drifted_operation,)}
    )

    report = _run(code_case, bundle=drifted_bundle)

    assert not report.passed
    assert {"code_bundle_paths", "code_bundle_entrypoint"}.issubset(
        {check.check_id for check in report.blockers}
    )


def test_implementation_fingerprint_must_bind_exact_genome_and_bundle(
    code_case: _Case,
    tmp_path: Path,
) -> None:
    wrong_implementation = code_candidate_implementation_fingerprint(
        genome_exact_sha256=code_case.candidate.fingerprints["exact"],
        bundle_hash=content_sha256(b"different materialization bundle"),
    )
    mismatched_candidate = build_candidate_record(
        run_id=code_case.contract.run_id,
        genome=code_case.candidate.genome,
        creator=code_case.candidate.creator,
        operator=code_case.candidate.operator,
        implementation_fingerprint=wrong_implementation,
        metadata={"touched_paths": list(code_case.spec.touched_paths)},
    )
    mismatched_workspace = _materialize(
        snapshot=code_case.snapshot,
        blob_root=code_case.blob_root,
        workspaces_root=tmp_path / "mismatched-workspaces",
        candidate=mismatched_candidate,
        contract=code_case.contract,
        spec=code_case.spec,
        bundle=code_case.bundle,
    )

    report = _run(
        code_case,
        candidate=mismatched_candidate,
        candidate_workspace=mismatched_workspace.root,
    )

    assert not report.passed
    assert {check.check_id for check in report.blockers} == {
        "code_implementation_fingerprint"
    }


def test_artifact_path_declaration_drift_is_rejected(code_case: _Case) -> None:
    report = run_code_candidate_preflight(
        candidate=code_case.candidate,
        contract=code_case.contract,
        spec=code_case.spec,
        snapshot_root=code_case.snapshot.root,
        bundle=code_case.bundle,
        candidate_workspace=code_case.workspace.root,
        touched_paths=code_case.spec.touched_paths,
        artifact_metadata={"touched_paths": ["candidate/other.py"]},
    )

    assert not report.passed
    assert "code_touched_paths" in {check.check_id for check in report.blockers}


def test_side_effect_source_is_parsed_but_never_executed(
    code_case: _Case,
    tmp_path: Path,
) -> None:
    side_effect = tmp_path / "must-not-exist.txt"
    source = (
        "from pathlib import Path\n"
        f"Path({str(side_effect)!r}).write_text('executed')\n"
        "def build_model(config):\n"
        "    return config['model']\n"
    ).encode()
    source_hash = _store_blob(code_case.blob_root, source)
    spec = code_case.spec.model_copy(update={"patch_sha256": source_hash})
    bundle = CodeMaterializationBundle(
        base_snapshot_id=code_case.snapshot.manifest.snapshot_id,
        code_spec_sha256=code_candidate_spec_sha256(spec),
        operations=(
            CodeBlobOperation(
                path=spec.entrypoint,
                action="replace",
                content_sha256=source_hash,
                expected_base_sha256=_snapshot_hash(
                    code_case.snapshot, spec.entrypoint
                ),
            ),
        ),
    )
    implementation = code_candidate_implementation_fingerprint(
        genome_exact_sha256=code_case.candidate.fingerprints["exact"],
        bundle_hash=bundle_sha256(bundle),
    )
    candidate = build_candidate_record(
        run_id=code_case.contract.run_id,
        genome=code_case.candidate.genome,
        creator=code_case.candidate.creator,
        operator=code_case.candidate.operator,
        implementation_fingerprint=implementation,
        metadata={"touched_paths": list(spec.touched_paths)},
    )
    workspace = _materialize(
        snapshot=code_case.snapshot,
        blob_root=code_case.blob_root,
        workspaces_root=tmp_path / "side-effect-workspaces",
        candidate=candidate,
        contract=code_case.contract,
        spec=spec,
        bundle=bundle,
    )

    report = run_code_candidate_preflight(
        candidate=candidate,
        contract=code_case.contract,
        spec=spec,
        snapshot_root=code_case.snapshot.root,
        bundle=bundle,
        candidate_workspace=workspace.root,
        touched_paths=spec.touched_paths,
    )

    assert report.passed
    assert not side_effect.exists()


def _run(
    case: _Case,
    *,
    candidate: CandidateRecord | None = None,
    spec: CodeCandidateSpec | None = None,
    bundle: CodeMaterializationBundle | None = None,
    candidate_workspace: object = _DEFAULT_WORKSPACE,
) -> PreflightReport:
    selected_workspace = (
        case.workspace.root
        if candidate_workspace is _DEFAULT_WORKSPACE
        else candidate_workspace
    )
    assert selected_workspace is None or isinstance(selected_workspace, Path)
    return run_code_candidate_preflight(
        candidate=candidate or case.candidate,
        contract=case.contract,
        spec=spec or case.spec,
        snapshot_root=case.snapshot.root,
        bundle=bundle or case.bundle,
        candidate_workspace=selected_workspace,
        touched_paths=(spec or case.spec).touched_paths,
    )


def _materialize(
    *,
    snapshot: SnapshotHandle,
    blob_root: Path,
    workspaces_root: Path,
    candidate: CandidateRecord,
    contract: ResearchTaskContract,
    spec: CodeCandidateSpec,
    bundle: CodeMaterializationBundle,
) -> MaterializedCodeWorkspace:
    return materialize_code_workspace(
        snapshot_root=snapshot.root,
        blob_root=blob_root,
        workspaces_root=workspaces_root,
        candidate_id=candidate.candidate_id,
        bundle=bundle,
        allowed_paths=contract.allowed_paths,
        protected_paths=contract.forbidden_paths,
        expected_touched_paths=spec.touched_paths,
        expected_entrypoint=spec.entrypoint,
    )


def _snapshot_hash(snapshot: SnapshotHandle, path: str) -> str:
    return next(item.sha256 for item in snapshot.manifest.files if item.path == path)


def _store_blob(root: Path, payload: bytes) -> str:
    digest = content_sha256(payload)
    path = content_blob_path(root, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return digest


def _make_writable(root: Path) -> None:
    for path in root.rglob("*"):
        mode = path.stat(follow_symlinks=False).st_mode
        if stat.S_ISDIR(mode):
            path.chmod(0o755)
        elif not path.is_symlink():
            path.chmod(0o644)
    root.chmod(0o755)
