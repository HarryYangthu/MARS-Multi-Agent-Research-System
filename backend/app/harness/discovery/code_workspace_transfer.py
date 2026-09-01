"""Deterministic, fail-closed transfer packages for code workspaces.

The package contains the complete materialized workspace plus the baseline
bytes replaced by the mutation bundle.  A remote worker can therefore rebuild
the immutable snapshot and reuse :func:`verify_code_workspace` immediately
before launching a trusted adapter.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import tempfile
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.harness.discovery.canonical import canonical_json, stable_hash
from app.harness.discovery.code_materialization import (
    CodeMaterializationBundle,
    MaterializationError,
    MaterializedCodeWorkspace,
    bundle_sha256,
    verify_code_workspace,
)
from app.harness.discovery.snapshots import (
    SnapshotError,
    SnapshotManifest,
    verify_snapshot,
)

_WORKSPACE_MANIFEST = ".mars_code_workspace_manifest.json"
_SNAPSHOT_MANIFEST = "snapshot_manifest.json"
_WORKSPACE_PREFIX = "workspace"
_BASE_PREFIX = "base"
_MAX_TRANSFER_FILES = 10_000
_MAX_TRANSFER_BYTES = 1024 * 1024 * 1024
_MAX_RECEIPT_BYTES = 16 * 1024 * 1024
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"


class CodeWorkspaceTransferError(ValueError):
    """A workspace transfer could not be proven safe and reproducible."""


class CodeWorkspaceTransferReceipt(BaseModel):
    """Self-contained provenance required to verify an extracted workspace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["code_workspace_transfer.v1"] = "code_workspace_transfer.v1"
    candidate_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    workspace_id: str = Field(pattern=r"^codews_[0-9a-f]{24}$")
    base_snapshot_id: str = Field(pattern=r"^snap_[0-9a-f]{24}$")
    snapshot_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    code_spec_sha256: str = Field(pattern=_SHA256_PATTERN)
    bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    workspace_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    archive_sha256: str = Field(pattern=_SHA256_PATTERN)
    archive_size_bytes: int = Field(ge=1, le=_MAX_TRANSFER_BYTES)
    extracted_file_count: int = Field(ge=1, le=_MAX_TRANSFER_FILES)
    extracted_total_bytes: int = Field(ge=1, le=_MAX_TRANSFER_BYTES)
    expected_touched_paths: tuple[str, ...] = Field(min_length=1)
    expected_entrypoint: str = Field(min_length=1)
    snapshot_manifest: SnapshotManifest
    bundle: CodeMaterializationBundle

    @model_validator(mode="after")
    def validate_provenance(self) -> CodeWorkspaceTransferReceipt:
        if self.snapshot_manifest.snapshot_id != self.base_snapshot_id:
            raise ValueError("snapshot manifest does not match base_snapshot_id")
        if stable_hash(self.snapshot_manifest.model_dump(mode="json")) != (
            self.snapshot_manifest_sha256
        ):
            raise ValueError("snapshot manifest hash mismatch")
        if self.bundle.base_snapshot_id != self.base_snapshot_id:
            raise ValueError("bundle does not match base_snapshot_id")
        if self.bundle.code_spec_sha256 != self.code_spec_sha256:
            raise ValueError("bundle does not match code_spec_sha256")
        if bundle_sha256(self.bundle) != self.bundle_sha256:
            raise ValueError("bundle hash mismatch")
        operation_paths = tuple(operation.path for operation in self.bundle.operations)
        if tuple(sorted(self.expected_touched_paths)) != operation_paths:
            raise ValueError("expected_touched_paths do not match bundle operations")
        if len(self.expected_touched_paths) != len(set(self.expected_touched_paths)):
            raise ValueError("expected_touched_paths must be unique")
        if self.expected_entrypoint not in operation_paths:
            raise ValueError("expected_entrypoint is not materialized by the bundle")
        return self


@dataclass(frozen=True)
class CodeWorkspaceTransferPackage:
    archive_path: Path
    receipt_path: Path
    receipt: CodeWorkspaceTransferReceipt
    receipt_sha256: str


@dataclass(frozen=True)
class VerifiedCodeWorkspaceTransfer:
    workspace: MaterializedCodeWorkspace
    receipt: CodeWorkspaceTransferReceipt
    receipt_sha256: str


def build_code_workspace_transfer(
    *,
    workspace_root: Path,
    snapshot_root: Path,
    bundle: CodeMaterializationBundle,
    expected_touched_paths: tuple[str, ...],
    expected_entrypoint: str,
    transfer_root: Path,
    max_files: int = _MAX_TRANSFER_FILES,
    max_bytes: int = _MAX_TRANSFER_BYTES,
) -> CodeWorkspaceTransferPackage:
    """Verify provenance and publish a deterministic tar plus canonical receipt."""

    _validate_limits(max_files=max_files, max_bytes=max_bytes)
    snapshot = verify_snapshot(snapshot_root)
    initial = verify_code_workspace(workspace_root)
    verified = verify_code_workspace(
        workspace_root,
        expected_candidate_id=initial.manifest.candidate_id,
        expected_base_snapshot_id=snapshot.manifest.snapshot_id,
        expected_code_spec_sha256=bundle.code_spec_sha256,
        expected_bundle_sha256=bundle_sha256(bundle),
        expected_snapshot_root=snapshot.root,
        expected_bundle=bundle,
        expected_touched_paths=expected_touched_paths,
        expected_entrypoint=expected_entrypoint,
        max_workspace_files=max_files,
        max_workspace_bytes=max_bytes,
    )

    entries = _transfer_sources(
        workspace=verified,
        snapshot_root=snapshot.root,
        bundle=bundle,
    )
    extracted_total = sum(_regular_file_size(source, label=name) for name, source in entries)
    if len(entries) > max_files:
        raise CodeWorkspaceTransferError("workspace transfer exceeds max_files")
    if extracted_total > max_bytes:
        raise CodeWorkspaceTransferError("workspace transfer exceeds max_bytes")

    destination_root = transfer_root.expanduser().resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    if transfer_root.is_symlink() or not destination_root.is_dir():
        raise CodeWorkspaceTransferError(
            "transfer_root must be an existing non-symlink directory"
        )
    archive_path = destination_root / f"{verified.manifest.workspace_id}.tar"
    temporary_archive = _temporary_path(destination_root, suffix=".tar")
    try:
        _write_deterministic_tar(temporary_archive, entries)
        archive_size = temporary_archive.stat().st_size
        if archive_size > max_bytes:
            raise CodeWorkspaceTransferError("workspace archive exceeds max_bytes")
        archive_hash = _sha256_file(temporary_archive)
        os.replace(temporary_archive, archive_path)
    finally:
        temporary_archive.unlink(missing_ok=True)

    receipt = CodeWorkspaceTransferReceipt(
        candidate_id=verified.manifest.candidate_id,
        workspace_id=verified.manifest.workspace_id,
        base_snapshot_id=snapshot.manifest.snapshot_id,
        snapshot_manifest_sha256=stable_hash(
            snapshot.manifest.model_dump(mode="json")
        ),
        code_spec_sha256=bundle.code_spec_sha256,
        bundle_sha256=bundle_sha256(bundle),
        workspace_manifest_sha256=verified.manifest_sha256,
        archive_sha256=archive_hash,
        archive_size_bytes=archive_path.stat().st_size,
        extracted_file_count=len(entries),
        extracted_total_bytes=extracted_total,
        expected_touched_paths=tuple(sorted(expected_touched_paths)),
        expected_entrypoint=expected_entrypoint,
        snapshot_manifest=snapshot.manifest,
        bundle=bundle,
    )
    receipt_bytes = _canonical_receipt_bytes(receipt)
    if len(receipt_bytes) > _MAX_RECEIPT_BYTES:
        raise CodeWorkspaceTransferError("workspace receipt exceeds receipt byte limit")
    receipt_path = destination_root / f"{verified.manifest.workspace_id}.receipt.json"
    temporary_receipt = _temporary_path(destination_root, suffix=".json")
    try:
        temporary_receipt.write_bytes(receipt_bytes)
        temporary_receipt.chmod(0o644)
        os.replace(temporary_receipt, receipt_path)
    finally:
        temporary_receipt.unlink(missing_ok=True)
    package = CodeWorkspaceTransferPackage(
        archive_path=archive_path,
        receipt_path=receipt_path,
        receipt=receipt,
        receipt_sha256=_sha256_bytes(receipt_bytes),
    )
    validate_code_workspace_transfer_package(package)
    return package


def validate_code_workspace_transfer_package(
    package: CodeWorkspaceTransferPackage,
) -> None:
    """Verify package files still match the resolver-provided receipt."""

    archive = _regular_file(package.archive_path, label="workspace archive")
    receipt_path = _regular_file(package.receipt_path, label="workspace receipt")
    if archive.stat().st_size != package.receipt.archive_size_bytes:
        raise CodeWorkspaceTransferError("workspace archive size mismatch")
    if _sha256_file(archive) != package.receipt.archive_sha256:
        raise CodeWorkspaceTransferError("workspace archive hash mismatch")
    receipt_bytes = _read_bounded(
        receipt_path,
        max_bytes=_MAX_RECEIPT_BYTES,
        label="workspace receipt",
    )
    if _sha256_bytes(receipt_bytes) != package.receipt_sha256:
        raise CodeWorkspaceTransferError("workspace receipt hash mismatch")
    parsed = _parse_receipt(receipt_bytes)
    if parsed != package.receipt:
        raise CodeWorkspaceTransferError("workspace receipt content mismatch")


def verify_and_extract_code_workspace_transfer(
    *,
    archive_path: Path,
    receipt_path: Path,
    destination: Path,
    max_files: int = _MAX_TRANSFER_FILES,
    max_bytes: int = _MAX_TRANSFER_BYTES,
) -> VerifiedCodeWorkspaceTransfer:
    """Safely extract and strongly re-verify a transferred code workspace."""

    _validate_limits(max_files=max_files, max_bytes=max_bytes)
    archive = _regular_file(archive_path, label="workspace archive")
    receipt_file = _regular_file(receipt_path, label="workspace receipt")
    receipt_bytes = _read_bounded(
        receipt_file,
        max_bytes=_MAX_RECEIPT_BYTES,
        label="workspace receipt",
    )
    receipt = _parse_receipt(receipt_bytes)
    receipt_sha = _sha256_bytes(receipt_bytes)
    archive_size = archive.stat().st_size
    if archive_size > max_bytes or archive_size != receipt.archive_size_bytes:
        raise CodeWorkspaceTransferError("workspace archive size mismatch")
    if _sha256_file(archive) != receipt.archive_sha256:
        raise CodeWorkspaceTransferError("workspace archive hash mismatch")
    if receipt.extracted_file_count > max_files:
        raise CodeWorkspaceTransferError("workspace transfer exceeds max_files")
    if receipt.extracted_total_bytes > max_bytes:
        raise CodeWorkspaceTransferError("workspace transfer exceeds max_bytes")

    parent, normalized_destination = _validated_destination(destination)
    staging = Path(tempfile.mkdtemp(prefix=".mars-workspace-transfer-", dir=parent))
    try:
        workspace_root, base_root = _extract_archive(
            archive=archive,
            staging=staging,
            receipt=receipt,
            max_files=max_files,
            max_bytes=max_bytes,
        )
        snapshot_root = _rebuild_snapshot(
            staging=staging,
            workspace_root=workspace_root,
            base_root=base_root,
            receipt=receipt,
        )
        verified_snapshot = verify_snapshot(snapshot_root)
        verified = verify_code_workspace(
            workspace_root,
            expected_candidate_id=receipt.candidate_id,
            expected_base_snapshot_id=receipt.base_snapshot_id,
            expected_code_spec_sha256=receipt.code_spec_sha256,
            expected_bundle_sha256=receipt.bundle_sha256,
            expected_snapshot_root=verified_snapshot.root,
            expected_bundle=receipt.bundle,
            expected_touched_paths=receipt.expected_touched_paths,
            expected_entrypoint=receipt.expected_entrypoint,
            max_workspace_files=max_files,
            max_workspace_bytes=max_bytes,
        )
        if verified.manifest.workspace_id != receipt.workspace_id:
            raise CodeWorkspaceTransferError("workspace identity mismatch")
        if verified.manifest_sha256 != receipt.workspace_manifest_sha256:
            raise CodeWorkspaceTransferError("workspace manifest hash mismatch")
        try:
            os.replace(workspace_root, normalized_destination)
        except OSError as exc:
            raise CodeWorkspaceTransferError(
                f"cannot publish verified workspace: {exc}"
            ) from exc
        published = verify_code_workspace(
            normalized_destination,
            expected_candidate_id=receipt.candidate_id,
            expected_base_snapshot_id=receipt.base_snapshot_id,
            expected_code_spec_sha256=receipt.code_spec_sha256,
            expected_bundle_sha256=receipt.bundle_sha256,
            max_workspace_files=max_files,
            max_workspace_bytes=max_bytes,
        )
        return VerifiedCodeWorkspaceTransfer(
            workspace=published,
            receipt=receipt,
            receipt_sha256=receipt_sha,
        )
    except (MaterializationError, SnapshotError, OSError, tarfile.TarError) as exc:
        if isinstance(exc, CodeWorkspaceTransferError):
            raise
        raise CodeWorkspaceTransferError(str(exc)) from exc
    finally:
        if staging.exists():
            _remove_staging_tree(staging)


def _transfer_sources(
    *,
    workspace: MaterializedCodeWorkspace,
    snapshot_root: Path,
    bundle: CodeMaterializationBundle,
) -> tuple[tuple[str, Path], ...]:
    entries: list[tuple[str, Path]] = [
        (f"{_WORKSPACE_PREFIX}/{_WORKSPACE_MANIFEST}", workspace.manifest_path)
    ]
    entries.extend(
        (f"{_WORKSPACE_PREFIX}/{item.path}", workspace.root / item.path)
        for item in workspace.manifest.files
    )
    entries.extend(
        (f"{_BASE_PREFIX}/{operation.path}", snapshot_root / operation.path)
        for operation in bundle.operations
        if operation.action == "replace"
    )
    return tuple(sorted(entries, key=lambda item: item[0]))


def _write_deterministic_tar(
    destination: Path,
    entries: tuple[tuple[str, Path], ...],
) -> None:
    with tarfile.open(destination, mode="w:", format=tarfile.GNU_FORMAT) as archive:
        for name, source in entries:
            normalized = _safe_member_name(name)
            path = _regular_file(source, label=normalized)
            metadata = path.stat(follow_symlinks=False)
            info = tarfile.TarInfo(normalized)
            info.size = metadata.st_size
            info.mode = 0o644
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            with path.open("rb") as stream:
                archive.addfile(info, stream)


def _extract_archive(
    *,
    archive: Path,
    staging: Path,
    receipt: CodeWorkspaceTransferReceipt,
    max_files: int,
    max_bytes: int,
) -> tuple[Path, Path]:
    workspace_root = staging / _WORKSPACE_PREFIX
    base_root = staging / _BASE_PREFIX
    workspace_root.mkdir(mode=0o755)
    base_root.mkdir(mode=0o755)
    expected_base = {
        operation.path for operation in receipt.bundle.operations if operation.action == "replace"
    }
    actual_base: set[str] = set()
    names: set[str] = set()
    total = 0
    count = 0
    with tarfile.open(archive, mode="r:") as source:
        for member in source.getmembers():
            name = _safe_member_name(member.name)
            if name in names:
                raise CodeWorkspaceTransferError(f"duplicate archive member: {name}")
            names.add(name)
            if not member.isfile() or member.issym() or member.islnk():
                raise CodeWorkspaceTransferError(
                    f"archive member must be a regular file: {name}"
                )
            if stat.S_IMODE(member.mode) != 0o644:
                raise CodeWorkspaceTransferError(
                    f"archive member mode must be 0644: {name}"
                )
            count += 1
            total += member.size
            if count > max_files:
                raise CodeWorkspaceTransferError("workspace transfer exceeds max_files")
            if member.size < 0 or total > max_bytes:
                raise CodeWorkspaceTransferError("workspace transfer exceeds max_bytes")
            relative = PurePosixPath(name)
            prefix = relative.parts[0]
            nested = PurePosixPath(*relative.parts[1:]).as_posix()
            if prefix == _BASE_PREFIX:
                actual_base.add(nested)
            elif prefix != _WORKSPACE_PREFIX:
                raise CodeWorkspaceTransferError(
                    f"unexpected archive member prefix: {name}"
                )
            target = staging.joinpath(*relative.parts)
            _write_archive_member(source, member, target, staging=staging)
    if count != receipt.extracted_file_count or total != receipt.extracted_total_bytes:
        raise CodeWorkspaceTransferError("archive inventory does not match receipt")
    if actual_base != expected_base:
        raise CodeWorkspaceTransferError("archive baseline inventory does not match bundle")
    if f"{_WORKSPACE_PREFIX}/{_WORKSPACE_MANIFEST}" not in names:
        raise CodeWorkspaceTransferError("archive workspace manifest is missing")
    for root in (workspace_root, base_root):
        for directory in (path for path in root.rglob("*") if path.is_dir()):
            directory.chmod(0o755)
        root.chmod(0o755)
    return workspace_root, base_root


def _write_archive_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    target: Path,
    *,
    staging: Path,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    resolved_parent = target.parent.resolve(strict=True)
    try:
        resolved_parent.relative_to(staging.resolve(strict=True))
    except ValueError as exc:
        raise CodeWorkspaceTransferError(
            f"archive member escapes staging root: {member.name}"
        ) from exc
    if target.exists() or target.is_symlink():
        raise CodeWorkspaceTransferError(f"archive target already exists: {member.name}")
    stream = archive.extractfile(member)
    if stream is None:
        raise CodeWorkspaceTransferError(f"cannot read archive member: {member.name}")
    written = 0
    try:
        with target.open("xb") as output:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > member.size:
                    raise CodeWorkspaceTransferError(
                        f"archive member exceeds declared size: {member.name}"
                    )
                output.write(chunk)
    finally:
        stream.close()
    if written != member.size:
        raise CodeWorkspaceTransferError(
            f"archive member size mismatch: {member.name}"
        )
    target.chmod(0o644)


def _rebuild_snapshot(
    *,
    staging: Path,
    workspace_root: Path,
    base_root: Path,
    receipt: CodeWorkspaceTransferReceipt,
) -> Path:
    snapshot_root = staging / "snapshot" / receipt.base_snapshot_id
    snapshot_root.mkdir(parents=True, mode=0o755)
    replaced = {
        operation.path for operation in receipt.bundle.operations if operation.action == "replace"
    }
    for item in receipt.snapshot_manifest.files:
        source_root = base_root if item.path in replaced else workspace_root
        source = source_root / item.path
        source = _regular_file(source, label=f"snapshot source {item.path}")
        target = snapshot_root / item.path
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        shutil.copyfile(source, target, follow_symlinks=False)
        target.chmod(0o555 if item.executable else 0o444)
    manifest_path = snapshot_root / _SNAPSHOT_MANIFEST
    manifest_path.write_text(
        canonical_json(receipt.snapshot_manifest.model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o444)
    directories = sorted(
        (path for path in snapshot_root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        directory.chmod(0o555)
    snapshot_root.chmod(0o555)
    return snapshot_root


def _validated_destination(destination: Path) -> tuple[Path, Path]:
    expanded = destination.expanduser()
    if expanded.is_symlink() or expanded.exists():
        raise CodeWorkspaceTransferError("workspace destination must not already exist")
    parent = expanded.parent.resolve(strict=True)
    normalized = parent / expanded.name
    if not expanded.name or expanded.name in {".", ".."}:
        raise CodeWorkspaceTransferError("workspace destination name is invalid")
    return parent, normalized


def _remove_staging_tree(root: Path) -> None:
    """Make only this private staging tree writable before cleanup."""

    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            continue
        try:
            path.chmod(0o755 if path.is_dir() else 0o644)
        except FileNotFoundError:
            continue
    root.chmod(0o755)
    shutil.rmtree(root)


def _safe_member_name(value: str) -> str:
    if "\\" in value or "\x00" in value:
        raise CodeWorkspaceTransferError("archive member path is not normalized POSIX")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or value != path.as_posix()
        or len(path.parts) < 2
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise CodeWorkspaceTransferError("archive member path is unsafe")
    return value


def _parse_receipt(payload: bytes) -> CodeWorkspaceTransferReceipt:
    try:
        text = payload.decode("utf-8", errors="strict")
        receipt = CodeWorkspaceTransferReceipt.model_validate_json(text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise CodeWorkspaceTransferError(f"workspace receipt is invalid: {exc}") from exc
    if payload != _canonical_receipt_bytes(receipt):
        raise CodeWorkspaceTransferError("workspace receipt is not canonical JSON")
    return receipt


def _canonical_receipt_bytes(receipt: CodeWorkspaceTransferReceipt) -> bytes:
    return (
        canonical_json(receipt.model_dump(mode="json")) + "\n"
    ).encode("utf-8")


def _temporary_path(root: Path, *, suffix: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=".mars-transfer-", suffix=suffix, dir=root)
    os.close(descriptor)
    return Path(raw_path)


def _regular_file(path: Path, *, label: str) -> Path:
    if path.is_symlink():
        raise CodeWorkspaceTransferError(f"{label} must not be a symbolic link")
    try:
        mode = path.stat(follow_symlinks=False).st_mode
    except OSError as exc:
        raise CodeWorkspaceTransferError(f"cannot inspect {label}: {exc}") from exc
    if not stat.S_ISREG(mode):
        raise CodeWorkspaceTransferError(f"{label} must be a regular file")
    return path


def _regular_file_size(path: Path, *, label: str) -> int:
    return _regular_file(path, label=label).stat(follow_symlinks=False).st_size


def _read_bounded(path: Path, *, max_bytes: int, label: str) -> bytes:
    if path.stat(follow_symlinks=False).st_size > max_bytes:
        raise CodeWorkspaceTransferError(f"{label} exceeds byte limit")
    with path.open("rb") as stream:
        payload = stream.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise CodeWorkspaceTransferError(f"{label} exceeds byte limit")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _sha256_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _validate_limits(*, max_files: int, max_bytes: int) -> None:
    if not 0 < max_files <= _MAX_TRANSFER_FILES:
        raise CodeWorkspaceTransferError(
            f"max_files must be between 1 and {_MAX_TRANSFER_FILES}"
        )
    if not 0 < max_bytes <= _MAX_TRANSFER_BYTES:
        raise CodeWorkspaceTransferError(
            f"max_bytes must be between 1 and {_MAX_TRANSFER_BYTES}"
        )
