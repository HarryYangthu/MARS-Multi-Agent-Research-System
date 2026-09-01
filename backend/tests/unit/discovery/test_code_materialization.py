from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.harness.discovery.canonical import canonical_json, stable_hash
from app.harness.discovery.code_materialization import (
    CodeBlobOperation,
    CodeMaterializationBundle,
    MaterializedCodeWorkspace,
    MaterializationError,
    bundle_sha256,
    code_identity_fingerprint,
    content_blob_path,
    content_sha256,
    materialize_code_workspace as _materialize_code_workspace,
    verify_code_workspace,
)
from app.harness.discovery.snapshots import (
    SnapshotHandle,
    SnapshotPolicy,
    create_snapshot,
)


@pytest.fixture
def source_snapshot(tmp_path: Path) -> Iterator[tuple[SnapshotHandle, Path]]:
    source = tmp_path / "source"
    (source / "pkg").mkdir(parents=True)
    (source / "pkg" / "model.py").write_text("VALUE = 1\n", encoding="utf-8")
    train = source / "train.py"
    train.write_text("print('baseline')\n", encoding="utf-8")
    train.chmod(0o755)
    snapshot = create_snapshot(
        source_root=source,
        cache_root=tmp_path / "snapshots",
        project="pimc",
        source_ref="working-tree:test",
        policy=SnapshotPolicy(allowed_paths=("pkg/", "train.py")),
    )
    yield snapshot, source
    _make_writable(snapshot.root)


def test_materializes_add_replace_idempotently_and_binds_code_identity(
    tmp_path: Path,
    source_snapshot: tuple[SnapshotHandle, Path],
) -> None:
    snapshot, source = source_snapshot
    blob_root = tmp_path / "blobs"
    replacement = _store_blob(blob_root, b"VALUE = 2\n")
    addition = _store_blob(blob_root, b"def helper() -> int:\n    return 3\n")
    bundle = _bundle(
        snapshot,
        (
            CodeBlobOperation(
                path="pkg/model.py",
                action="replace",
                content_sha256=replacement,
                expected_base_sha256=_snapshot_hash(snapshot, "pkg/model.py"),
            ),
            CodeBlobOperation(
                path="pkg/new.py",
                action="add",
                content_sha256=addition,
            ),
        ),
    )

    first = materialize_code_workspace(
        snapshot_root=snapshot.root,
        blob_root=blob_root,
        workspaces_root=tmp_path / "workspaces",
        candidate_id="cand_safe_001",
        bundle=bundle,
        allowed_paths=("pkg",),
    )
    second = materialize_code_workspace(
        snapshot_root=snapshot.root,
        blob_root=blob_root,
        workspaces_root=tmp_path / "workspaces",
        candidate_id="cand_safe_001",
        bundle=bundle,
        allowed_paths=("pkg",),
    )

    assert first.root == second.root
    assert first.manifest_sha256 == second.manifest_sha256
    assert (first.root / "pkg/model.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert (first.root / "pkg/new.py").read_text(encoding="utf-8").startswith(
        "def helper"
    )
    assert (source / "pkg/model.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (snapshot.root / "pkg/model.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert tuple(item.path for item in first.manifest.files) == (
        "pkg/model.py",
        "pkg/new.py",
        "train.py",
    )
    assert first.manifest_path.name not in {item.path for item in first.manifest.files}
    assert tuple(item.path for item in first.manifest.directories) == ("pkg",)
    assert first.manifest.root_mode == "0755"
    for path in (*tuple(first.root.rglob("*.py")), first.manifest_path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert verify_code_workspace(first.root).manifest == first.manifest
    assert (
        verify_code_workspace(
            first.root,
            expected_snapshot_root=snapshot.root,
            expected_bundle=bundle,
            expected_touched_paths=tuple(
                operation.path for operation in bundle.operations
            ),
            expected_entrypoint="pkg/model.py",
        ).manifest
        == first.manifest
    )

    digest = bundle_sha256(bundle)
    assert digest == bundle_sha256(CodeMaterializationBundle.model_validate(bundle))
    identity = code_identity_fingerprint(
        genome_fingerprint="sha256:" + "1" * 64,
        bundle_hash=digest,
    )
    assert identity == code_identity_fingerprint(
        genome_fingerprint="sha256:" + "1" * 64,
        bundle_hash=digest,
    )
    assert identity != code_identity_fingerprint(
        genome_fingerprint="sha256:" + "1" * 64,
        bundle_hash=content_sha256(b"another bundle"),
    )


def test_rejects_wrong_expected_base_hash_without_partial_workspace(
    tmp_path: Path,
    source_snapshot: tuple[SnapshotHandle, Path],
) -> None:
    snapshot, _source = source_snapshot
    blob_root = tmp_path / "blobs"
    replacement = _store_blob(blob_root, b"VALUE = 2\n")
    bundle = _bundle(
        snapshot,
        (
            CodeBlobOperation(
                path="pkg/model.py",
                action="replace",
                content_sha256=replacement,
                expected_base_sha256=content_sha256(b"wrong base"),
            ),
        ),
    )
    workspaces = tmp_path / "workspaces"

    with pytest.raises(MaterializationError, match="base hash mismatch"):
        materialize_code_workspace(
            snapshot_root=snapshot.root,
            blob_root=blob_root,
            workspaces_root=workspaces,
            candidate_id="cand_wrong_base",
            bundle=bundle,
            allowed_paths=("pkg",),
        )

    assert list(workspaces.iterdir()) == []


def test_rejects_escape_empty_wildcard_outside_and_protected_paths(
    tmp_path: Path,
    source_snapshot: tuple[SnapshotHandle, Path],
) -> None:
    snapshot, _source = source_snapshot
    with pytest.raises(ValueError, match="safe relative POSIX path"):
        CodeBlobOperation(
            path="../escape.py",
            action="add",
            content_sha256=content_sha256(b"safe text\n"),
        )

    blob_root = tmp_path / "blobs"
    replacement = _store_blob(blob_root, b"VALUE = 2\n")
    bundle = _bundle(
        snapshot,
        (
            CodeBlobOperation(
                path="pkg/model.py",
                action="replace",
                content_sha256=replacement,
                expected_base_sha256=_snapshot_hash(snapshot, "pkg/model.py"),
            ),
        ),
    )

    for allowed, message in (
        ((), "must not be empty"),
        (("pkg/*",), "wildcard"),
        (("other",), "outside allowed_paths"),
    ):
        with pytest.raises(MaterializationError, match=message):
            materialize_code_workspace(
                snapshot_root=snapshot.root,
                blob_root=blob_root,
                workspaces_root=tmp_path / f"workspaces-{len(message)}",
                candidate_id="cand_policy",
                bundle=bundle,
                allowed_paths=allowed,
            )

    with pytest.raises(MaterializationError, match="operation path is protected"):
        materialize_code_workspace(
            snapshot_root=snapshot.root,
            blob_root=blob_root,
            workspaces_root=tmp_path / "workspaces-protected",
            candidate_id="cand_protected",
            bundle=bundle,
            allowed_paths=("pkg",),
            protected_paths=("pkg/model.py:BaselineModel",),
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        (b"\xff\xfe", "UTF-8"),
        (b"VALUE = 'safe'\x00\n", "NUL"),
        (b"VALUE = 1\x01\n", "binary control"),
    ),
)
def test_rejects_binary_and_nul_blobs(
    tmp_path: Path,
    source_snapshot: tuple[SnapshotHandle, Path],
    payload: bytes,
    message: str,
) -> None:
    snapshot, _source = source_snapshot
    blob_root = tmp_path / "blobs"
    digest = _store_blob(blob_root, payload)
    bundle = _bundle(
        snapshot,
        (
            CodeBlobOperation(
                path="pkg/new.py",
                action="add",
                content_sha256=digest,
            ),
        ),
    )
    workspaces = tmp_path / "workspaces"

    with pytest.raises(MaterializationError, match=message):
        materialize_code_workspace(
            snapshot_root=snapshot.root,
            blob_root=blob_root,
            workspaces_root=workspaces,
            candidate_id="cand_bad_blob",
            bundle=bundle,
            allowed_paths=("pkg",),
        )

    assert list(workspaces.iterdir()) == []


def test_rejects_blob_root_blob_and_workspace_symlinks(
    tmp_path: Path,
    source_snapshot: tuple[SnapshotHandle, Path],
) -> None:
    snapshot, _source = source_snapshot
    payload = b"VALUE = 2\n"
    digest = content_sha256(payload)
    bundle = _bundle(
        snapshot,
        (
            CodeBlobOperation(
                path="pkg/new.py",
                action="add",
                content_sha256=digest,
            ),
        ),
    )

    real_blob_root = tmp_path / "real-blobs"
    _store_blob(real_blob_root, payload)
    blob_root_link = tmp_path / "blob-root-link"
    blob_root_link.symlink_to(real_blob_root, target_is_directory=True)
    with pytest.raises(MaterializationError, match="symlink ancestor"):
        materialize_code_workspace(
            snapshot_root=snapshot.root,
            blob_root=blob_root_link,
            workspaces_root=tmp_path / "workspaces-root-link-case",
            candidate_id="cand_blob_root_link",
            bundle=bundle,
            allowed_paths=("pkg",),
        )

    linked_blob_root = tmp_path / "linked-blob"
    blob_path = content_blob_path(linked_blob_root, digest)
    blob_path.parent.mkdir(parents=True)
    outside_blob = tmp_path / "outside.txt"
    outside_blob.write_bytes(payload)
    blob_path.symlink_to(outside_blob)
    with pytest.raises(MaterializationError, match="symlink ancestor"):
        materialize_code_workspace(
            snapshot_root=snapshot.root,
            blob_root=linked_blob_root,
            workspaces_root=tmp_path / "workspaces-blob-link-case",
            candidate_id="cand_blob_link",
            bundle=bundle,
            allowed_paths=("pkg",),
        )

    workspaces_real = tmp_path / "workspaces-real"
    workspaces_real.mkdir()
    workspaces_link = tmp_path / "workspaces-link"
    workspaces_link.symlink_to(workspaces_real, target_is_directory=True)
    with pytest.raises(MaterializationError, match="symlink ancestor"):
        materialize_code_workspace(
            snapshot_root=snapshot.root,
            blob_root=real_blob_root,
            workspaces_root=workspaces_link,
            candidate_id="cand_workspace_root_link",
            bundle=bundle,
            allowed_paths=("pkg",),
        )

    outside_workspace = tmp_path / "outside-workspace"
    outside_workspace.mkdir()
    destination_link = workspaces_real / "cand_destination_link"
    destination_link.symlink_to(outside_workspace, target_is_directory=True)
    with pytest.raises(MaterializationError, match="symlink ancestor"):
        materialize_code_workspace(
            snapshot_root=snapshot.root,
            blob_root=real_blob_root,
            workspaces_root=workspaces_real,
            candidate_id="cand_destination_link",
            bundle=bundle,
            allowed_paths=("pkg",),
        )


def test_atomic_failure_cleans_staging_and_stale_staging_does_not_block_retry(
    tmp_path: Path,
    source_snapshot: tuple[SnapshotHandle, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, _source = source_snapshot
    blob_root = tmp_path / "blobs"
    digest = _store_blob(blob_root, b"VALUE = 2\n")
    bundle = _bundle(
        snapshot,
        (
            CodeBlobOperation(
                path="pkg/new.py",
                action="add",
                content_sha256=digest,
            ),
        ),
    )
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    stale = workspaces / ".mars-code-workspace-stale"
    stale.mkdir()
    (stale / "incomplete").write_text("crash residue", encoding="utf-8")

    real_replace = os.replace

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("injected atomic rename failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(MaterializationError, match="atomic workspace publication failed"):
        materialize_code_workspace(
            snapshot_root=snapshot.root,
            blob_root=blob_root,
            workspaces_root=workspaces,
            candidate_id="cand_atomic",
            bundle=bundle,
            allowed_paths=("pkg",),
        )
    assert not (workspaces / "cand_atomic").exists()
    assert sorted(path.name for path in workspaces.iterdir()) == [stale.name]

    monkeypatch.setattr(os, "replace", real_replace)
    published = materialize_code_workspace(
        snapshot_root=snapshot.root,
        blob_root=blob_root,
        workspaces_root=workspaces,
        candidate_id="cand_atomic",
        bundle=bundle,
        allowed_paths=("pkg",),
    )
    assert published.root.name == "cand_atomic"
    assert stale.is_dir()


def test_verifier_and_idempotent_retry_reject_tamper_extra_and_symlink(
    tmp_path: Path,
    source_snapshot: tuple[SnapshotHandle, Path],
) -> None:
    snapshot, _source = source_snapshot
    blob_root = tmp_path / "blobs"
    digest = _store_blob(blob_root, b"VALUE = 2\n")
    bundle = _bundle(
        snapshot,
        (
            CodeBlobOperation(
                path="pkg/new.py",
                action="add",
                content_sha256=digest,
            ),
        ),
    )
    workspaces = tmp_path / "workspaces"

    tampered = materialize_code_workspace(
        snapshot_root=snapshot.root,
        blob_root=blob_root,
        workspaces_root=workspaces,
        candidate_id="cand_tampered",
        bundle=bundle,
        allowed_paths=("pkg",),
    )
    (tampered.root / "pkg/new.py").write_text("VALUE = 999\n", encoding="utf-8")
    with pytest.raises(MaterializationError, match="workspace file mismatch"):
        verify_code_workspace(tampered.root)
    with pytest.raises(MaterializationError, match="workspace file mismatch"):
        materialize_code_workspace(
            snapshot_root=snapshot.root,
            blob_root=blob_root,
            workspaces_root=workspaces,
            candidate_id="cand_tampered",
            bundle=bundle,
            allowed_paths=("pkg",),
        )
    assert (tampered.root / "pkg/new.py").read_text(encoding="utf-8") == "VALUE = 999\n"

    extra = materialize_code_workspace(
        snapshot_root=snapshot.root,
        blob_root=blob_root,
        workspaces_root=workspaces,
        candidate_id="cand_extra",
        bundle=bundle,
        allowed_paths=("pkg",),
    )
    (extra.root / "extra.py").write_text("EXTRA = True\n", encoding="utf-8")
    with pytest.raises(MaterializationError, match="file set"):
        verify_code_workspace(extra.root)

    linked = materialize_code_workspace(
        snapshot_root=snapshot.root,
        blob_root=blob_root,
        workspaces_root=workspaces,
        candidate_id="cand_linked",
        bundle=bundle,
        allowed_paths=("pkg",),
    )
    (linked.root / "outside-link.py").symlink_to(tmp_path / "outside.py")
    with pytest.raises(MaterializationError, match="symlink"):
        verify_code_workspace(linked.root)


def test_strong_verifier_rejects_self_consistent_forged_manifest(
    tmp_path: Path,
    source_snapshot: tuple[SnapshotHandle, Path],
) -> None:
    snapshot, _source = source_snapshot
    blob_root = tmp_path / "blobs"
    digest = _store_blob(blob_root, b"VALUE = 2\n")
    bundle = _bundle(
        snapshot,
        (
            CodeBlobOperation(
                path="pkg/new.py",
                action="add",
                content_sha256=digest,
            ),
        ),
    )
    workspace = materialize_code_workspace(
        snapshot_root=snapshot.root,
        blob_root=blob_root,
        workspaces_root=tmp_path / "workspaces",
        candidate_id="cand_forged",
        bundle=bundle,
        allowed_paths=("pkg",),
    )

    forged_payload = b"VALUE = 999\n"
    (workspace.root / "pkg/new.py").write_bytes(forged_payload)
    forged_files = tuple(
        item.model_copy(
            update={
                "sha256": content_sha256(forged_payload),
                "size_bytes": len(forged_payload),
            }
        )
        if item.path == "pkg/new.py"
        else item
        for item in workspace.manifest.files
    )
    identity = {
        "candidate_id": workspace.manifest.candidate_id,
        "base_snapshot_id": workspace.manifest.base_snapshot_id,
        "code_spec_sha256": workspace.manifest.code_spec_sha256,
        "bundle_sha256": workspace.manifest.bundle_sha256,
        "root_mode": workspace.manifest.root_mode,
        "directories": [
            item.model_dump(mode="json")
            for item in workspace.manifest.directories
        ],
        "files": [item.model_dump(mode="json") for item in forged_files],
    }
    forged_manifest = workspace.manifest.model_copy(
        update={
            "workspace_id": f"codews_{stable_hash(identity, prefix='')[:24]}",
            "files": forged_files,
        }
    )
    workspace.manifest_path.write_text(
        canonical_json(forged_manifest.model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )

    assert verify_code_workspace(workspace.root).manifest == forged_manifest
    with pytest.raises(MaterializationError, match="does not implement"):
        verify_code_workspace(
            workspace.root,
            expected_snapshot_root=snapshot.root,
            expected_bundle=bundle,
        )


def test_manifest_covers_all_directories_and_enforces_0755_modes(
    tmp_path: Path,
    source_snapshot: tuple[SnapshotHandle, Path],
) -> None:
    snapshot, _source = source_snapshot
    blob_root = tmp_path / "blobs"
    digest = _store_blob(blob_root, b"VALUE = 2\n")
    bundle = _bundle(
        snapshot,
        (
            CodeBlobOperation(
                path="pkg/new.py",
                action="add",
                content_sha256=digest,
            ),
        ),
    )
    workspace = materialize_code_workspace(
        snapshot_root=snapshot.root,
        blob_root=blob_root,
        workspaces_root=tmp_path / "workspaces",
        candidate_id="cand_directories",
        bundle=bundle,
        allowed_paths=("pkg",),
    )
    assert stat.S_IMODE(workspace.root.stat().st_mode) == 0o755
    assert stat.S_IMODE((workspace.root / "pkg").stat().st_mode) == 0o755

    empty = workspace.root / "empty"
    empty.mkdir()
    with pytest.raises(MaterializationError, match="directory set"):
        verify_code_workspace(workspace.root)
    empty.rmdir()

    forbidden = workspace.root / ".GIT"
    forbidden.mkdir()
    with pytest.raises(MaterializationError, match="hard-forbidden directory"):
        verify_code_workspace(workspace.root)
    forbidden.rmdir()

    package = workspace.root / "pkg"
    package.chmod(0o777)
    with pytest.raises(MaterializationError, match="directory mode must be 0755"):
        verify_code_workspace(workspace.root)
    package.chmod(0o755)

    workspace.root.chmod(0o777)
    with pytest.raises(MaterializationError, match="root mode must be 0755"):
        verify_code_workspace(workspace.root)
    workspace.root.chmod(0o755)


def test_rejects_operation_count_path_depth_and_blob_budgets(
    tmp_path: Path,
    source_snapshot: tuple[SnapshotHandle, Path],
) -> None:
    snapshot, _source = source_snapshot
    operations = tuple(
        CodeBlobOperation(
            path=f"pkg/generated_{index:03}.py",
            action="add",
            content_sha256=content_sha256(f"VALUE = {index}\n".encode()),
        )
        for index in range(65)
    )
    with pytest.raises(ValueError, match="at most 64"):
        _bundle(snapshot, operations)

    deep_path = "/".join((*tuple(f"level{index}" for index in range(16)), "x.py"))
    with pytest.raises(ValueError, match="path depth"):
        CodeBlobOperation(
            path=deep_path,
            action="add",
            content_sha256=content_sha256(b"VALUE = 1\n"),
        )

    blob_root = tmp_path / "blobs"
    first_digest = _store_blob(blob_root, b"123456\n")
    second_digest = _store_blob(blob_root, b"abcdef\n")
    budget_bundle = _bundle(
        snapshot,
        (
            CodeBlobOperation(
                path="pkg/a.py",
                action="add",
                content_sha256=first_digest,
            ),
            CodeBlobOperation(
                path="pkg/b.py",
                action="add",
                content_sha256=second_digest,
            ),
        ),
    )

    per_blob_root = tmp_path / "workspaces-per-blob"
    with pytest.raises(MaterializationError, match="exceeds max_blob_bytes"):
        materialize_code_workspace(
            snapshot_root=snapshot.root,
            blob_root=blob_root,
            workspaces_root=per_blob_root,
            candidate_id="cand_per_blob_budget",
            bundle=budget_bundle,
            allowed_paths=("pkg",),
            max_blob_bytes=6,
        )
    assert list(per_blob_root.iterdir()) == []

    total_root = tmp_path / "workspaces-total"
    with pytest.raises(MaterializationError, match="max_total_blob_bytes"):
        materialize_code_workspace(
            snapshot_root=snapshot.root,
            blob_root=blob_root,
            workspaces_root=total_root,
            candidate_id="cand_total_budget",
            bundle=budget_bundle,
            allowed_paths=("pkg",),
            max_blob_bytes=7,
            max_total_blob_bytes=13,
        )
    assert list(total_root.iterdir()) == []


def test_rejects_code_candidate_declaration_drift(
    tmp_path: Path,
    source_snapshot: tuple[SnapshotHandle, Path],
) -> None:
    snapshot, _source = source_snapshot
    blob_root = tmp_path / "blobs"
    first_digest = _store_blob(blob_root, b"A = 1\n")
    second_digest = _store_blob(blob_root, b"B = 2\n")
    bundle = _bundle(
        snapshot,
        (
            CodeBlobOperation(
                path="pkg/a.py",
                action="add",
                content_sha256=first_digest,
            ),
            CodeBlobOperation(
                path="pkg/b.py",
                action="add",
                content_sha256=second_digest,
            ),
        ),
    )

    with pytest.raises(MaterializationError, match="exactly match"):
        materialize_code_workspace(
            snapshot_root=snapshot.root,
            blob_root=blob_root,
            workspaces_root=tmp_path / "workspaces-touched",
            candidate_id="cand_touched_drift",
            bundle=bundle,
            allowed_paths=("pkg",),
            expected_touched_paths=("pkg/a.py",),
            expected_entrypoint="pkg/a.py",
        )

    with pytest.raises(MaterializationError, match="entrypoint"):
        materialize_code_workspace(
            snapshot_root=snapshot.root,
            blob_root=blob_root,
            workspaces_root=tmp_path / "workspaces-entrypoint",
            candidate_id="cand_entrypoint_drift",
            bundle=bundle,
            allowed_paths=("pkg",),
            expected_touched_paths=("pkg/a.py", "pkg/b.py"),
            expected_entrypoint="train.py",
        )


def test_rejects_snapshot_file_count_and_byte_footprint_before_copy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "many-source"
    (source / "pkg").mkdir(parents=True)
    for index in range(5):
        (source / "pkg" / f"file_{index}.py").write_text(
            f"VALUE = {index}\n",
            encoding="utf-8",
        )
    snapshot = create_snapshot(
        source_root=source,
        cache_root=tmp_path / "many-snapshots",
        project="pimc",
        source_ref="working-tree:many",
        policy=SnapshotPolicy(allowed_paths=("pkg/",)),
    )
    try:
        blob_root = tmp_path / "many-blobs"
        replacement = _store_blob(blob_root, b"VALUE = 99\n")
        bundle = _bundle(
            snapshot,
            (
                CodeBlobOperation(
                    path="pkg/file_0.py",
                    action="replace",
                    content_sha256=replacement,
                    expected_base_sha256=_snapshot_hash(snapshot, "pkg/file_0.py"),
                ),
            ),
        )

        file_limited = tmp_path / "workspaces-file-limited"
        with pytest.raises(MaterializationError, match="snapshot exceeds max_workspace_files"):
            materialize_code_workspace(
                snapshot_root=snapshot.root,
                blob_root=blob_root,
                workspaces_root=file_limited,
                candidate_id="cand_too_many_snapshot_files",
                bundle=bundle,
                allowed_paths=("pkg",),
                max_workspace_files=4,
            )
        assert list(file_limited.iterdir()) == []

        baseline_bytes = sum(item.size_bytes for item in snapshot.manifest.files)
        byte_limited = tmp_path / "workspaces-byte-limited"
        with pytest.raises(MaterializationError, match="snapshot exceeds max_workspace_bytes"):
            materialize_code_workspace(
                snapshot_root=snapshot.root,
                blob_root=blob_root,
                workspaces_root=byte_limited,
                candidate_id="cand_snapshot_too_large",
                bundle=bundle,
                allowed_paths=("pkg",),
                max_workspace_bytes=baseline_bytes - 1,
            )
        assert list(byte_limited.iterdir()) == []
    finally:
        _make_writable(snapshot.root)


def test_idempotent_reuse_rejects_stricter_workspace_limits(
    tmp_path: Path,
    source_snapshot: tuple[SnapshotHandle, Path],
) -> None:
    snapshot, _source = source_snapshot
    blob_root = tmp_path / "blobs"
    digest = _store_blob(blob_root, b"VALUE = 2\n")
    bundle = _bundle(
        snapshot,
        (
            CodeBlobOperation(
                path="pkg/new.py",
                action="add",
                content_sha256=digest,
            ),
        ),
    )
    workspaces = tmp_path / "workspaces"
    workspace = materialize_code_workspace(
        snapshot_root=snapshot.root,
        blob_root=blob_root,
        workspaces_root=workspaces,
        candidate_id="cand_reuse_budget",
        bundle=bundle,
        allowed_paths=("pkg",),
    )

    with pytest.raises(MaterializationError, match="workspace exceeds max_workspace_files"):
        materialize_code_workspace(
            snapshot_root=snapshot.root,
            blob_root=blob_root,
            workspaces_root=workspaces,
            candidate_id="cand_reuse_budget",
            bundle=bundle,
            allowed_paths=("pkg",),
            max_workspace_files=len(workspace.manifest.files) - 1,
        )

    workspace_bytes = sum(item.size_bytes for item in workspace.manifest.files)
    with pytest.raises(MaterializationError, match="workspace exceeds max_workspace_bytes"):
        materialize_code_workspace(
            snapshot_root=snapshot.root,
            blob_root=blob_root,
            workspaces_root=workspaces,
            candidate_id="cand_reuse_budget",
            bundle=bundle,
            allowed_paths=("pkg",),
            max_workspace_bytes=workspace_bytes - 1,
        )


def _bundle(
    snapshot: SnapshotHandle,
    operations: tuple[CodeBlobOperation, ...],
) -> CodeMaterializationBundle:
    return CodeMaterializationBundle(
        base_snapshot_id=snapshot.manifest.snapshot_id,
        code_spec_sha256=content_sha256(b"code candidate spec"),
        operations=operations,
    )


def materialize_code_workspace(
    *,
    snapshot_root: Path,
    blob_root: Path,
    workspaces_root: Path,
    candidate_id: str,
    bundle: CodeMaterializationBundle,
    allowed_paths: tuple[str, ...],
    protected_paths: tuple[str, ...] = (),
    expected_touched_paths: tuple[str, ...] | None = None,
    expected_entrypoint: str | None = None,
    max_blob_bytes: int = 16 * 1024 * 1024,
    max_total_blob_bytes: int = 64 * 1024 * 1024,
    max_workspace_files: int = 10_000,
    max_workspace_bytes: int = 1024 * 1024 * 1024,
) -> MaterializedCodeWorkspace:
    """Keep tests terse while always exercising the mandatory declaration gate."""
    operation_paths = tuple(operation.path for operation in bundle.operations)
    return _materialize_code_workspace(
        snapshot_root=snapshot_root,
        blob_root=blob_root,
        workspaces_root=workspaces_root,
        candidate_id=candidate_id,
        bundle=bundle,
        allowed_paths=allowed_paths,
        expected_touched_paths=(
            operation_paths
            if expected_touched_paths is None
            else expected_touched_paths
        ),
        expected_entrypoint=(
            operation_paths[0] if expected_entrypoint is None else expected_entrypoint
        ),
        protected_paths=protected_paths,
        max_blob_bytes=max_blob_bytes,
        max_total_blob_bytes=max_total_blob_bytes,
        max_workspace_files=max_workspace_files,
        max_workspace_bytes=max_workspace_bytes,
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
        if path.is_dir():
            path.chmod(0o755)
        elif not path.is_symlink():
            path.chmod(0o644)
    root.chmod(0o755)
