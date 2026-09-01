from __future__ import annotations

import hashlib
import io
from pathlib import Path
import tarfile

import pytest

from app.harness.discovery.canonical import canonical_json
from app.harness.discovery.code_materialization import (
    CodeBlobOperation,
    CodeMaterializationBundle,
    content_blob_path,
    content_sha256,
    materialize_code_workspace,
)
from app.harness.discovery.code_workspace_transfer import (
    CodeWorkspaceTransferError,
    CodeWorkspaceTransferPackage,
    build_code_workspace_transfer,
    verify_and_extract_code_workspace_transfer,
)
from app.harness.discovery.snapshots import SnapshotPolicy, create_snapshot


def test_transfer_is_deterministic_and_round_trips_with_strong_verification(
    tmp_path: Path,
) -> None:
    first = _build_package(tmp_path, transfer_name="transfer-a")
    second = build_code_workspace_transfer(
        workspace_root=first.workspace_root,
        snapshot_root=first.snapshot_root,
        bundle=first.bundle,
        expected_touched_paths=("model.py",),
        expected_entrypoint="model.py",
        transfer_root=tmp_path / "transfer-b",
    )

    assert first.package.archive_path.read_bytes() == second.archive_path.read_bytes()
    assert first.package.receipt_path.read_bytes() == second.receipt_path.read_bytes()

    restored = verify_and_extract_code_workspace_transfer(
        archive_path=first.package.archive_path,
        receipt_path=first.package.receipt_path,
        destination=tmp_path / "restored",
    )

    assert restored.workspace.manifest.workspace_id == first.package.receipt.workspace_id
    assert restored.workspace.manifest_sha256 == (
        first.package.receipt.workspace_manifest_sha256
    )
    assert (restored.workspace.root / "model.py").read_text(encoding="utf-8") == (
        "def build_model(config):\n    return {'version': 2}\n"
    )


def test_transfer_rejects_archive_hash_tampering(tmp_path: Path) -> None:
    built = _build_package(tmp_path)
    payload = bytearray(built.package.archive_path.read_bytes())
    payload[512] ^= 1
    built.package.archive_path.write_bytes(payload)

    with pytest.raises(CodeWorkspaceTransferError, match="archive hash mismatch"):
        verify_and_extract_code_workspace_transfer(
            archive_path=built.package.archive_path,
            receipt_path=built.package.receipt_path,
            destination=tmp_path / "restored",
        )


@pytest.mark.parametrize(
    ("member_name", "member_type", "message"),
    (
        ("../escape.py", tarfile.REGTYPE, "path is unsafe"),
        ("workspace/link.py", tarfile.SYMTYPE, "regular file"),
    ),
)
def test_transfer_rejects_traversal_and_links_even_with_matching_archive_hash(
    tmp_path: Path,
    member_name: str,
    member_type: bytes,
    message: str,
) -> None:
    built = _build_package(tmp_path)
    malicious = tmp_path / "malicious.tar"
    _append_member(
        source=built.package.archive_path,
        destination=malicious,
        member_name=member_name,
        member_type=member_type,
    )
    receipt = built.package.receipt.model_copy(
        update={
            "archive_sha256": _sha256_file(malicious),
            "archive_size_bytes": malicious.stat().st_size,
            "extracted_file_count": built.package.receipt.extracted_file_count + 1,
        }
    )
    receipt_path = tmp_path / "malicious.receipt.json"
    receipt_path.write_text(
        canonical_json(receipt.model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(CodeWorkspaceTransferError, match=message):
        verify_and_extract_code_workspace_transfer(
            archive_path=malicious,
            receipt_path=receipt_path,
            destination=tmp_path / "restored",
        )
    assert not (tmp_path / "escape.py").exists()


def test_transfer_enforces_stricter_file_and_byte_limits(tmp_path: Path) -> None:
    built = _build_package(tmp_path)

    with pytest.raises(CodeWorkspaceTransferError, match="max_files"):
        verify_and_extract_code_workspace_transfer(
            archive_path=built.package.archive_path,
            receipt_path=built.package.receipt_path,
            destination=tmp_path / "too-many",
            max_files=1,
        )
    with pytest.raises(CodeWorkspaceTransferError, match="archive size mismatch"):
        verify_and_extract_code_workspace_transfer(
            archive_path=built.package.archive_path,
            receipt_path=built.package.receipt_path,
            destination=tmp_path / "too-large",
            max_bytes=1,
        )


class _BuiltPackage:
    def __init__(
        self,
        *,
        package: CodeWorkspaceTransferPackage,
        workspace_root: Path,
        snapshot_root: Path,
        bundle: CodeMaterializationBundle,
    ) -> None:
        self.package = package
        self.workspace_root = workspace_root
        self.snapshot_root = snapshot_root
        self.bundle = bundle


def _build_package(
    tmp_path: Path,
    *,
    transfer_name: str = "transfer",
) -> _BuiltPackage:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    baseline = b"def build_model(config):\n    return {'version': 1}\n"
    (source / "model.py").write_bytes(baseline)
    snapshot = create_snapshot(
        source_root=source,
        cache_root=tmp_path / "snapshots",
        project="pimc",
        source_ref="test-baseline",
        policy=SnapshotPolicy(allowed_paths=("model.py",)),
    )

    replacement = b"def build_model(config):\n    return {'version': 2}\n"
    digest = content_sha256(replacement)
    blob_root = tmp_path / "blobs"
    blob_path = content_blob_path(blob_root, digest)
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_bytes(replacement)
    bundle = CodeMaterializationBundle(
        base_snapshot_id=snapshot.manifest.snapshot_id,
        code_spec_sha256=content_sha256(b"code-spec"),
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
        workspaces_root=tmp_path / "workspaces",
        candidate_id="candidate-1",
        bundle=bundle,
        allowed_paths=("model.py",),
        expected_touched_paths=("model.py",),
        expected_entrypoint="model.py",
    )
    package = build_code_workspace_transfer(
        workspace_root=workspace.root,
        snapshot_root=snapshot.root,
        bundle=bundle,
        expected_touched_paths=("model.py",),
        expected_entrypoint="model.py",
        transfer_root=tmp_path / transfer_name,
    )
    return _BuiltPackage(
        package=package,
        workspace_root=workspace.root,
        snapshot_root=snapshot.root,
        bundle=bundle,
    )


def _append_member(
    *,
    source: Path,
    destination: Path,
    member_name: str,
    member_type: bytes,
) -> None:
    with tarfile.open(source, mode="r:") as original, tarfile.open(
        destination,
        mode="w:",
        format=tarfile.GNU_FORMAT,
    ) as output:
        for member in original.getmembers():
            stream = original.extractfile(member)
            assert stream is not None
            output.addfile(member, stream)
            stream.close()
        extra = tarfile.TarInfo(member_name)
        extra.type = member_type
        extra.mode = 0o644
        extra.mtime = 0
        if member_type == tarfile.SYMTYPE:
            extra.linkname = "model.py"
            output.addfile(extra)
        else:
            payload = b""
            extra.size = len(payload)
            output.addfile(extra, io.BytesIO(payload))


def _sha256_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
