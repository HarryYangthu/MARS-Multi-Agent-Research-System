"""Fail-closed materialization of content-addressed code candidate workspaces.

This module deliberately applies no patch language and invokes no process.  A
candidate is expressed as sorted add/replace operations whose UTF-8 payloads
already live in a caller-owned content-addressed blob store.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.harness.discovery.canonical import canonical_json, stable_hash
from app.harness.discovery.snapshots import (
    SnapshotError,
    SnapshotFile,
    SnapshotHandle,
    SnapshotManifest,
    verify_snapshot,
)

_WORKSPACE_MANIFEST = ".mars_code_workspace_manifest.json"
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HARD_FORBIDDEN_PARTS = frozenset({".git", ".hg", ".svn", "__pycache__"})
_HARD_FORBIDDEN_NAMES = frozenset(
    {".env", ".env.local", ".env.production", _WORKSPACE_MANIFEST}
)
_HARD_FORBIDDEN_SUFFIXES = frozenset(
    {".ckpt", ".key", ".mat", ".npy", ".npz", ".pem", ".pkl", ".pt", ".pth"}
)
_GLOB_TOKENS = frozenset("*?[]")
_MAX_OPERATIONS = 64
_MAX_OPERATION_PATH_DEPTH = 16
_DEFAULT_MAX_BLOB_BYTES = 16 * 1024 * 1024
_DEFAULT_MAX_TOTAL_BLOB_BYTES = 64 * 1024 * 1024
_HARD_MAX_WORKSPACE_FILES = 10_000
_HARD_MAX_WORKSPACE_BYTES = 1024 * 1024 * 1024
_DEFAULT_MAX_WORKSPACE_FILES = _HARD_MAX_WORKSPACE_FILES
_DEFAULT_MAX_WORKSPACE_BYTES = _HARD_MAX_WORKSPACE_BYTES


class MaterializationError(ValueError):
    """Raised when code materialization cannot be proven safe and reproducible."""


class CodeBlobOperation(BaseModel):
    """One exact-file mutation backed by a content-addressed text blob."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    action: Literal["add", "replace"]
    content_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_base_sha256: str | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = _safe_relative_path(value)
        if len(PurePosixPath(normalized).parts) > _MAX_OPERATION_PATH_DEPTH:
            raise ValueError(
                f"operation path depth must not exceed {_MAX_OPERATION_PATH_DEPTH}"
            )
        return normalized

    @model_validator(mode="after")
    def validate_base_precondition(self) -> CodeBlobOperation:
        if self.action == "add" and self.expected_base_sha256 is not None:
            raise ValueError("add operations must not declare expected_base_sha256")
        if self.action == "replace" and (
            self.expected_base_sha256 is None
            or _SHA256.fullmatch(self.expected_base_sha256) is None
        ):
            raise ValueError("replace operations require a valid expected_base_sha256")
        return self


class CodeMaterializationBundle(BaseModel):
    """Canonical mutation bundle whose hash can be bound to a genome identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["code_materialization.v1"] = "code_materialization.v1"
    base_snapshot_id: str = Field(pattern=r"^snap_[0-9a-f]{24}$")
    code_spec_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    operations: tuple[CodeBlobOperation, ...] = Field(
        min_length=1,
        max_length=_MAX_OPERATIONS,
    )

    @model_validator(mode="after")
    def validate_unique_sorted_paths(self) -> CodeMaterializationBundle:
        paths = tuple(operation.path for operation in self.operations)
        if paths != tuple(sorted(paths)):
            raise ValueError("bundle operations must be sorted by path")
        if len(paths) != len(set(paths)):
            raise ValueError("bundle operation paths must be unique")
        return self


class CodeWorkspaceFile(BaseModel):
    """One regular, non-executable file in a materialized workspace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    mode: Literal["0644"] = "0644"

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _safe_relative_path(value)


class CodeWorkspaceDirectory(BaseModel):
    """One declared directory in a materialized workspace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    mode: Literal["0755"] = "0755"

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _safe_relative_path(value)


class CodeWorkspaceManifest(BaseModel):
    """Complete deterministic inventory of a published candidate workspace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["code_workspace_manifest.v1"] = "code_workspace_manifest.v1"
    workspace_id: str = Field(pattern=r"^codews_[0-9a-f]{24}$")
    candidate_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    base_snapshot_id: str = Field(pattern=r"^snap_[0-9a-f]{24}$")
    code_spec_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    bundle_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    root_mode: Literal["0755"] = "0755"
    directories: tuple[CodeWorkspaceDirectory, ...] = ()
    files: tuple[CodeWorkspaceFile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_sorted_paths(self) -> CodeWorkspaceManifest:
        directories = tuple(item.path for item in self.directories)
        if directories != tuple(sorted(directories)):
            raise ValueError("workspace directories must be sorted by path")
        if len(directories) != len(set(directories)):
            raise ValueError("workspace directory paths must be unique")
        paths = tuple(item.path for item in self.files)
        if paths != tuple(sorted(paths)):
            raise ValueError("workspace files must be sorted by path")
        if len(paths) != len(set(paths)):
            raise ValueError("workspace file paths must be unique")
        if _WORKSPACE_MANIFEST in paths:
            raise ValueError("workspace manifest must not hash itself")
        if set(directories) & set(paths):
            raise ValueError("workspace path cannot be both a file and directory")
        return self


@dataclass(frozen=True)
class MaterializedCodeWorkspace:
    root: Path
    manifest_path: Path
    manifest: CodeWorkspaceManifest
    manifest_sha256: str


@dataclass(frozen=True)
class _WorkspaceInventory:
    root_mode: Literal["0755"]
    directories: tuple[CodeWorkspaceDirectory, ...]
    files: tuple[CodeWorkspaceFile, ...]


def content_sha256(content: bytes) -> str:
    """Return the canonical digest used as a code blob address."""
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def content_blob_path(blob_root: Path, digest: str) -> Path:
    """Map a validated digest to its one allowed CAS location."""
    if _SHA256.fullmatch(digest) is None:
        raise MaterializationError("blob digest must be a lowercase sha256 address")
    return blob_root / "sha256" / digest.removeprefix("sha256:")


def bundle_sha256(bundle: CodeMaterializationBundle) -> str:
    """Hash the complete canonical mutation contract."""
    return stable_hash(bundle.model_dump(mode="json"))


def code_identity_fingerprint(*, genome_fingerprint: str, bundle_hash: str) -> str:
    """Bind an existing genome identity to exact code without changing its model."""
    if _SHA256.fullmatch(genome_fingerprint) is None:
        raise MaterializationError(
            "genome_fingerprint must be a lowercase sha256 address"
        )
    if _SHA256.fullmatch(bundle_hash) is None:
        raise MaterializationError("bundle_hash must be a lowercase sha256 address")
    return stable_hash(
        {
            "schema_id": "genome_code_identity.v1",
            "genome_fingerprint": genome_fingerprint,
            "bundle_sha256": bundle_hash,
        }
    )


def materialize_code_workspace(
    *,
    snapshot_root: Path,
    blob_root: Path,
    workspaces_root: Path,
    candidate_id: str,
    bundle: CodeMaterializationBundle,
    allowed_paths: tuple[str, ...],
    expected_touched_paths: tuple[str, ...],
    expected_entrypoint: str,
    protected_paths: tuple[str, ...] = (),
    max_blob_bytes: int = _DEFAULT_MAX_BLOB_BYTES,
    max_total_blob_bytes: int = _DEFAULT_MAX_TOTAL_BLOB_BYTES,
    max_workspace_files: int = _DEFAULT_MAX_WORKSPACE_FILES,
    max_workspace_bytes: int = _DEFAULT_MAX_WORKSPACE_BYTES,
) -> MaterializedCodeWorkspace:
    """Publish an isolated workspace after validating every input and output byte."""
    if _SAFE_IDENTIFIER.fullmatch(candidate_id) is None:
        raise MaterializationError("candidate_id contains unsafe characters")
    _validate_materialization_limits(
        bundle=bundle,
        max_blob_bytes=max_blob_bytes,
        max_total_blob_bytes=max_total_blob_bytes,
        max_workspace_files=max_workspace_files,
        max_workspace_bytes=max_workspace_bytes,
    )
    _validate_declared_paths(
        bundle=bundle,
        expected_touched_paths=expected_touched_paths,
        expected_entrypoint=expected_entrypoint,
    )
    allowed = _normalize_policy_paths(allowed_paths, label="allowed_paths")
    protected = _normalize_protected_paths(protected_paths)

    snapshot_directory = _existing_directory(snapshot_root, label="snapshot_root")
    try:
        snapshot = verify_snapshot(snapshot_directory)
    except SnapshotError as exc:
        raise MaterializationError(f"snapshot verification failed: {exc}") from exc
    if bundle.base_snapshot_id != snapshot.manifest.snapshot_id:
        raise MaterializationError("bundle base_snapshot_id does not match snapshot")

    cas_root = _existing_directory(blob_root, label="blob_root")
    workspace_parent = _directory_root(
        workspaces_root,
        create=True,
        label="workspaces_root",
    )
    destination = workspace_parent / candidate_id
    _assert_contained(workspace_parent, destination, label="candidate workspace")
    _assert_no_symlink_components(destination, label="candidate workspace")

    base_files = {item.path: item for item in snapshot.manifest.files}
    _validate_operations(
        bundle=bundle,
        base_files=base_files,
        allowed_paths=allowed,
        protected_paths=protected,
    )

    if _path_exists(destination):
        existing = verify_code_workspace(
            destination,
            expected_candidate_id=candidate_id,
            expected_base_snapshot_id=snapshot.manifest.snapshot_id,
            expected_code_spec_sha256=bundle.code_spec_sha256,
            expected_bundle_sha256=bundle_sha256(bundle),
            expected_snapshot_root=snapshot.root,
            expected_bundle=bundle,
            expected_touched_paths=expected_touched_paths,
            expected_entrypoint=expected_entrypoint,
            max_blob_bytes=max_blob_bytes,
            max_total_blob_bytes=max_total_blob_bytes,
            max_workspace_files=max_workspace_files,
            max_workspace_bytes=max_workspace_bytes,
        )
        return existing

    _preflight_workspace_limits(
        snapshot=snapshot.manifest,
        bundle=bundle,
        blob_root=cas_root,
        max_blob_bytes=max_blob_bytes,
        max_total_blob_bytes=max_total_blob_bytes,
        max_workspace_files=max_workspace_files,
        max_workspace_bytes=max_workspace_bytes,
    )
    temporary = Path(
        tempfile.mkdtemp(prefix=".mars-code-workspace-", dir=workspace_parent)
    )
    try:
        _assert_contained(workspace_parent, temporary, label="temporary workspace")
        _assert_no_symlink_components(temporary, label="temporary workspace")
        _copy_snapshot(snapshot, temporary)
        total_blob_bytes = 0
        for operation in bundle.operations:
            remaining_total = max_total_blob_bytes - total_blob_bytes
            if remaining_total <= 0:
                raise MaterializationError(
                    "mutation bundle exceeds max_total_blob_bytes"
                )
            read_limit = min(max_blob_bytes, remaining_total)
            limit_name = (
                "max_total_blob_bytes"
                if remaining_total < max_blob_bytes
                else "max_blob_bytes"
            )
            payload = _read_code_blob(
                cas_root,
                operation.content_sha256,
                read_limit,
                limit_name=limit_name,
            )
            total_blob_bytes += len(payload)
            if total_blob_bytes > max_total_blob_bytes:
                raise MaterializationError("mutation bundle exceeds max_total_blob_bytes")
            target = temporary / operation.path
            _assert_contained(temporary, target, label=f"operation path {operation.path}")
            _assert_no_symlink_components(target, label=f"operation path {operation.path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            _assert_no_symlink_components(
                target.parent,
                label=f"operation parent {operation.path}",
            )
            target.write_bytes(payload)
            target.chmod(0o644)
            del payload
        _fix_published_modes(temporary)

        manifest = _build_manifest(
            root=temporary,
            candidate_id=candidate_id,
            base_snapshot_id=snapshot.manifest.snapshot_id,
            code_spec_sha256=bundle.code_spec_sha256,
            bundle_hash=bundle_sha256(bundle),
        )
        manifest_path = temporary / _WORKSPACE_MANIFEST
        manifest_path.write_text(
            canonical_json(manifest.model_dump(mode="json")) + "\n",
            encoding="utf-8",
        )
        manifest_path.chmod(0o644)
        staged = verify_code_workspace(
            temporary,
            expected_candidate_id=candidate_id,
            expected_base_snapshot_id=snapshot.manifest.snapshot_id,
            expected_code_spec_sha256=bundle.code_spec_sha256,
            expected_bundle_sha256=bundle_sha256(bundle),
            expected_snapshot_root=snapshot.root,
            expected_bundle=bundle,
            expected_touched_paths=expected_touched_paths,
            expected_entrypoint=expected_entrypoint,
            max_blob_bytes=max_blob_bytes,
            max_total_blob_bytes=max_total_blob_bytes,
            max_workspace_files=max_workspace_files,
            max_workspace_bytes=max_workspace_bytes,
        )

        try:
            os.replace(temporary, destination)
        except OSError as exc:
            if not _path_exists(destination):
                raise MaterializationError(
                    f"atomic workspace publication failed: {exc}"
                ) from exc
            raced = verify_code_workspace(
                destination,
                expected_candidate_id=candidate_id,
                expected_base_snapshot_id=snapshot.manifest.snapshot_id,
                expected_code_spec_sha256=bundle.code_spec_sha256,
                expected_bundle_sha256=bundle_sha256(bundle),
                expected_snapshot_root=snapshot.root,
                expected_bundle=bundle,
                expected_touched_paths=expected_touched_paths,
                expected_entrypoint=expected_entrypoint,
                max_blob_bytes=max_blob_bytes,
                max_total_blob_bytes=max_total_blob_bytes,
                max_workspace_files=max_workspace_files,
                max_workspace_bytes=max_workspace_bytes,
            )
            if _path_exists(temporary):
                shutil.rmtree(temporary)
            return raced
        published = verify_code_workspace(
            destination,
            expected_candidate_id=candidate_id,
            expected_base_snapshot_id=snapshot.manifest.snapshot_id,
            expected_code_spec_sha256=bundle.code_spec_sha256,
            expected_bundle_sha256=bundle_sha256(bundle),
            expected_snapshot_root=snapshot.root,
            expected_bundle=bundle,
            expected_touched_paths=expected_touched_paths,
            expected_entrypoint=expected_entrypoint,
            max_blob_bytes=max_blob_bytes,
            max_total_blob_bytes=max_total_blob_bytes,
            max_workspace_files=max_workspace_files,
            max_workspace_bytes=max_workspace_bytes,
        )
        return published
    except Exception:
        if _path_exists(temporary):
            shutil.rmtree(temporary)
        raise


def verify_code_workspace(
    root: Path,
    *,
    expected_candidate_id: str | None = None,
    expected_base_snapshot_id: str | None = None,
    expected_code_spec_sha256: str | None = None,
    expected_bundle_sha256: str | None = None,
    expected_snapshot_root: Path | None = None,
    expected_bundle: CodeMaterializationBundle | None = None,
    expected_touched_paths: tuple[str, ...] | None = None,
    expected_entrypoint: str | None = None,
    max_blob_bytes: int = _DEFAULT_MAX_BLOB_BYTES,
    max_total_blob_bytes: int = _DEFAULT_MAX_TOTAL_BLOB_BYTES,
    max_workspace_files: int = _DEFAULT_MAX_WORKSPACE_FILES,
    max_workspace_bytes: int = _DEFAULT_MAX_WORKSPACE_BYTES,
) -> MaterializedCodeWorkspace:
    """Re-hash a workspace; expected snapshot+bundle enable provenance checks."""
    if (expected_snapshot_root is None) != (expected_bundle is None):
        raise MaterializationError(
            "expected_snapshot_root and expected_bundle must be provided together"
        )
    if (expected_touched_paths is None) != (expected_entrypoint is None):
        raise MaterializationError(
            "expected_touched_paths and expected_entrypoint must be provided together"
        )
    if expected_touched_paths is not None and expected_bundle is None:
        raise MaterializationError(
            "declaration checks require expected_snapshot_root and expected_bundle"
        )
    _validate_workspace_limit_values(
        max_workspace_files=max_workspace_files,
        max_workspace_bytes=max_workspace_bytes,
    )
    resolved = _existing_directory(root, label="workspace root")
    manifest_path = resolved / _WORKSPACE_MANIFEST
    _assert_contained(resolved, manifest_path, label="workspace manifest")
    _assert_no_symlink_components(manifest_path, label="workspace manifest")
    try:
        manifest_mode = stat.S_IMODE(manifest_path.stat(follow_symlinks=False).st_mode)
        if manifest_mode != 0o644:
            raise MaterializationError("workspace manifest mode must be 0644")
        manifest_bytes = _read_regular_file(manifest_path, label="workspace manifest")
        manifest_text = manifest_bytes.decode("utf-8", errors="strict")
        manifest = CodeWorkspaceManifest.model_validate_json(manifest_text)
    except UnicodeDecodeError as exc:
        raise MaterializationError("workspace manifest is not UTF-8") from exc
    except (OSError, ValueError) as exc:
        if isinstance(exc, MaterializationError):
            raise
        raise MaterializationError(f"cannot read workspace manifest: {exc}") from exc

    canonical = canonical_json(manifest.model_dump(mode="json")) + "\n"
    if manifest_text != canonical:
        raise MaterializationError("workspace manifest is not canonical JSON")
    _check_expected("candidate_id", manifest.candidate_id, expected_candidate_id)
    _check_expected(
        "base_snapshot_id",
        manifest.base_snapshot_id,
        expected_base_snapshot_id,
    )
    _check_expected(
        "code_spec_sha256",
        manifest.code_spec_sha256,
        expected_code_spec_sha256,
    )
    _check_expected(
        "bundle_sha256",
        manifest.bundle_sha256,
        expected_bundle_sha256,
    )

    inventory = _inventory_workspace(resolved)
    if inventory.root_mode != manifest.root_mode:
        raise MaterializationError("workspace root mode does not match its manifest")
    if inventory.directories != manifest.directories:
        raise MaterializationError("workspace directory set does not match its manifest")
    if tuple(item.path for item in inventory.files) != tuple(
        item.path for item in manifest.files
    ):
        raise MaterializationError("workspace file set does not match its manifest")
    for expected, actual in zip(manifest.files, inventory.files, strict=True):
        if actual != expected:
            raise MaterializationError(f"workspace file mismatch: {expected.path}")
    _validate_workspace_inventory_limits(
        files=inventory.files,
        max_workspace_files=max_workspace_files,
        max_workspace_bytes=max_workspace_bytes,
    )

    expected_workspace_id = _derive_workspace_id(
        candidate_id=manifest.candidate_id,
        base_snapshot_id=manifest.base_snapshot_id,
        code_spec_sha256=manifest.code_spec_sha256,
        bundle_hash=manifest.bundle_sha256,
        root_mode=manifest.root_mode,
        directories=manifest.directories,
        files=manifest.files,
    )
    if manifest.workspace_id != expected_workspace_id:
        raise MaterializationError("workspace manifest identity mismatch")

    if expected_bundle is not None and expected_snapshot_root is not None:
        _validate_materialization_limits(
            bundle=expected_bundle,
            max_blob_bytes=max_blob_bytes,
            max_total_blob_bytes=max_total_blob_bytes,
            max_workspace_files=max_workspace_files,
            max_workspace_bytes=max_workspace_bytes,
        )
        if expected_touched_paths is not None and expected_entrypoint is not None:
            _validate_declared_paths(
                bundle=expected_bundle,
                expected_touched_paths=expected_touched_paths,
                expected_entrypoint=expected_entrypoint,
            )
        snapshot_directory = _existing_directory(
            expected_snapshot_root,
            label="expected_snapshot_root",
        )
        try:
            expected_snapshot = verify_snapshot(snapshot_directory)
        except SnapshotError as exc:
            raise MaterializationError(
                f"expected snapshot verification failed: {exc}"
            ) from exc
        if expected_bundle.base_snapshot_id != expected_snapshot.manifest.snapshot_id:
            raise MaterializationError(
                "expected bundle base_snapshot_id does not match expected snapshot"
            )
        _validate_snapshot_preconditions(
            bundle=expected_bundle,
            base_files={item.path: item for item in expected_snapshot.manifest.files},
        )
        _check_expected(
            "base_snapshot_id",
            manifest.base_snapshot_id,
            expected_snapshot.manifest.snapshot_id,
        )
        _check_expected(
            "code_spec_sha256",
            manifest.code_spec_sha256,
            expected_bundle.code_spec_sha256,
        )
        _check_expected(
            "bundle_sha256",
            manifest.bundle_sha256,
            bundle_sha256(expected_bundle),
        )
        _validate_materialized_content(
            manifest=manifest,
            snapshot=expected_snapshot.manifest,
            bundle=expected_bundle,
        )
        _validate_materialized_blob_sizes(
            manifest=manifest,
            bundle=expected_bundle,
            max_blob_bytes=max_blob_bytes,
            max_total_blob_bytes=max_total_blob_bytes,
        )
    return MaterializedCodeWorkspace(
        root=resolved,
        manifest_path=manifest_path,
        manifest=manifest,
        manifest_sha256=content_sha256(manifest_bytes),
    )


def _validate_operations(
    *,
    bundle: CodeMaterializationBundle,
    base_files: dict[str, SnapshotFile],
    allowed_paths: tuple[str, ...],
    protected_paths: tuple[str, ...],
) -> None:
    _validate_snapshot_preconditions(bundle=bundle, base_files=base_files)
    for operation in bundle.operations:
        if _hard_forbidden(operation.path):
            raise MaterializationError(f"operation path is hard-forbidden: {operation.path}")
        if not _matches_explicit(operation.path, allowed_paths):
            raise MaterializationError(f"operation path is outside allowed_paths: {operation.path}")
        if _matches_explicit(operation.path, protected_paths):
            raise MaterializationError(f"operation path is protected: {operation.path}")


def _validate_snapshot_preconditions(
    *,
    bundle: CodeMaterializationBundle,
    base_files: dict[str, SnapshotFile],
) -> None:
    for operation in bundle.operations:
        base = base_files.get(operation.path)
        if operation.action == "add":
            if base is not None:
                raise MaterializationError(
                    f"add path already exists in snapshot: {operation.path}"
                )
            continue
        if base is None:
            raise MaterializationError(
                f"replace path is absent from snapshot: {operation.path}"
            )
        if base.sha256 != operation.expected_base_sha256:
            raise MaterializationError(f"replace base hash mismatch: {operation.path}")


def _validate_materialization_limits(
    *,
    bundle: CodeMaterializationBundle,
    max_blob_bytes: int,
    max_total_blob_bytes: int,
    max_workspace_files: int,
    max_workspace_bytes: int,
) -> None:
    _validate_workspace_limit_values(
        max_workspace_files=max_workspace_files,
        max_workspace_bytes=max_workspace_bytes,
    )
    if not 0 < max_blob_bytes <= _DEFAULT_MAX_BLOB_BYTES:
        raise MaterializationError(
            f"max_blob_bytes must be between 1 and {_DEFAULT_MAX_BLOB_BYTES}"
        )
    if not 0 < max_total_blob_bytes <= _DEFAULT_MAX_TOTAL_BLOB_BYTES:
        raise MaterializationError(
            "max_total_blob_bytes must be between 1 and "
            f"{_DEFAULT_MAX_TOTAL_BLOB_BYTES}"
        )
    if not 0 < len(bundle.operations) <= _MAX_OPERATIONS:
        raise MaterializationError(
            f"mutation bundle must contain between 1 and {_MAX_OPERATIONS} operations"
        )
    for operation in bundle.operations:
        try:
            normalized = _safe_relative_path(operation.path)
        except ValueError as exc:
            raise MaterializationError(f"unsafe operation path: {operation.path}") from exc
        if normalized != operation.path:
            raise MaterializationError(
                f"operation path is not canonical: {operation.path}"
            )
        if len(PurePosixPath(normalized).parts) > _MAX_OPERATION_PATH_DEPTH:
            raise MaterializationError(
                "operation path depth exceeds "
                f"{_MAX_OPERATION_PATH_DEPTH}: {operation.path}"
            )


def _validate_workspace_limit_values(
    *,
    max_workspace_files: int,
    max_workspace_bytes: int,
) -> None:
    if not 0 < max_workspace_files <= _HARD_MAX_WORKSPACE_FILES:
        raise MaterializationError(
            "max_workspace_files must be between 1 and "
            f"{_HARD_MAX_WORKSPACE_FILES}"
        )
    if not 0 < max_workspace_bytes <= _HARD_MAX_WORKSPACE_BYTES:
        raise MaterializationError(
            "max_workspace_bytes must be between 1 and "
            f"{_HARD_MAX_WORKSPACE_BYTES}"
        )


def _preflight_workspace_limits(
    *,
    snapshot: SnapshotManifest,
    bundle: CodeMaterializationBundle,
    blob_root: Path,
    max_blob_bytes: int,
    max_total_blob_bytes: int,
    max_workspace_files: int,
    max_workspace_bytes: int,
) -> None:
    baseline_files = {item.path: item for item in snapshot.files}
    baseline_bytes = sum(item.size_bytes for item in snapshot.files)
    if len(snapshot.files) > max_workspace_files:
        raise MaterializationError("snapshot exceeds max_workspace_files")
    if baseline_bytes > max_workspace_bytes:
        raise MaterializationError("snapshot exceeds max_workspace_bytes")

    final_file_count = len(snapshot.files) + sum(
        operation.action == "add" for operation in bundle.operations
    )
    if final_file_count > max_workspace_files:
        raise MaterializationError("materialized workspace exceeds max_workspace_files")

    final_bytes = baseline_bytes
    total_blob_bytes = 0
    for operation in bundle.operations:
        blob_bytes = _code_blob_size(blob_root, operation.content_sha256)
        if blob_bytes > max_blob_bytes:
            raise MaterializationError(
                f"code blob exceeds max_blob_bytes: {operation.path}"
            )
        total_blob_bytes += blob_bytes
        if total_blob_bytes > max_total_blob_bytes:
            raise MaterializationError("mutation bundle exceeds max_total_blob_bytes")
        if operation.action == "replace":
            final_bytes -= baseline_files[operation.path].size_bytes
        final_bytes += blob_bytes
    if final_bytes > max_workspace_bytes:
        raise MaterializationError("materialized workspace exceeds max_workspace_bytes")


def _validate_workspace_inventory_limits(
    *,
    files: tuple[CodeWorkspaceFile, ...],
    max_workspace_files: int,
    max_workspace_bytes: int,
) -> None:
    if len(files) > max_workspace_files:
        raise MaterializationError("workspace exceeds max_workspace_files")
    if sum(item.size_bytes for item in files) > max_workspace_bytes:
        raise MaterializationError("workspace exceeds max_workspace_bytes")


def _validate_declared_paths(
    *,
    bundle: CodeMaterializationBundle,
    expected_touched_paths: tuple[str, ...],
    expected_entrypoint: str,
) -> None:
    try:
        normalized_touched = tuple(
            _safe_relative_path(path) for path in expected_touched_paths
        )
        normalized_entrypoint = _safe_relative_path(expected_entrypoint)
    except ValueError as exc:
        raise MaterializationError("code candidate declaration contains unsafe paths") from exc
    if len(normalized_touched) != len(set(normalized_touched)):
        raise MaterializationError("expected_touched_paths must be unique")
    operation_paths = tuple(operation.path for operation in bundle.operations)
    if tuple(sorted(normalized_touched)) != operation_paths:
        raise MaterializationError(
            "expected_touched_paths must exactly match bundle operation paths"
        )
    if normalized_entrypoint not in operation_paths:
        raise MaterializationError("expected_entrypoint must be materialized by the bundle")


def _validate_materialized_content(
    *,
    manifest: CodeWorkspaceManifest,
    snapshot: SnapshotManifest,
    bundle: CodeMaterializationBundle,
) -> None:
    expected = {item.path: item.sha256 for item in snapshot.files}
    for operation in bundle.operations:
        expected[operation.path] = operation.content_sha256
    actual = {item.path: item.sha256 for item in manifest.files}
    if actual != expected:
        raise MaterializationError("workspace content does not implement its mutation bundle")
    expected_directories = _parent_directories(tuple(expected))
    actual_directories = tuple(item.path for item in manifest.directories)
    if actual_directories != expected_directories:
        raise MaterializationError(
            "workspace directories do not implement the expected file tree"
        )


def _validate_materialized_blob_sizes(
    *,
    manifest: CodeWorkspaceManifest,
    bundle: CodeMaterializationBundle,
    max_blob_bytes: int,
    max_total_blob_bytes: int,
) -> None:
    files = {item.path: item for item in manifest.files}
    total = 0
    for operation in bundle.operations:
        size = files[operation.path].size_bytes
        if size > max_blob_bytes:
            raise MaterializationError(
                f"materialized blob exceeds max_blob_bytes: {operation.path}"
            )
        total += size
    if total > max_total_blob_bytes:
        raise MaterializationError("mutation bundle exceeds max_total_blob_bytes")


def _parent_directories(paths: tuple[str, ...]) -> tuple[str, ...]:
    directories: set[str] = set()
    for path in paths:
        parent = PurePosixPath(path).parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return tuple(sorted(directories))


def _copy_snapshot(snapshot: SnapshotHandle, destination: Path) -> None:
    for item in snapshot.manifest.files:
        source = snapshot.root / item.path
        target = destination / item.path
        _assert_contained(snapshot.root, source, label=f"snapshot file {item.path}")
        _assert_no_symlink_components(source, label=f"snapshot file {item.path}")
        _assert_contained(destination, target, label=f"workspace file {item.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_components(target.parent, label=f"workspace parent {item.path}")
        shutil.copyfile(source, target, follow_symlinks=False)
        target.chmod(0o644)


def _code_blob_size(blob_root: Path, digest: str) -> int:
    path = content_blob_path(blob_root, digest)
    _assert_contained(blob_root, path, label="code blob")
    _assert_no_symlink_components(path, label="code blob")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MaterializationError(f"cannot inspect code blob size: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise MaterializationError("code blob must be a regular file")
        return metadata.st_size
    finally:
        os.close(descriptor)


def _read_code_blob(
    blob_root: Path,
    digest: str,
    max_bytes: int,
    *,
    limit_name: str,
) -> bytes:
    path = content_blob_path(blob_root, digest)
    _assert_contained(blob_root, path, label="code blob")
    _assert_no_symlink_components(path, label="code blob")
    payload = _read_regular_file(
        path,
        label="code blob",
        max_bytes=max_bytes,
        max_bytes_label=limit_name,
    )
    if content_sha256(payload) != digest:
        raise MaterializationError("code blob content does not match its address")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise MaterializationError("code blob must be UTF-8 text") from exc
    if "\x00" in text:
        raise MaterializationError("code blob must not contain NUL bytes")
    if any(
        (character < " " and character not in "\t\n\r") or character == "\x7f"
        for character in text
    ):
        raise MaterializationError("code blob must not contain binary control bytes")
    return payload


def _read_regular_file(
    path: Path,
    *,
    label: str,
    max_bytes: int | None = None,
    max_bytes_label: str = "max_blob_bytes",
) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MaterializationError(f"cannot open {label}: {exc}") from exc
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode):
            raise MaterializationError(f"{label} must be a regular file")
        if max_bytes is not None and os.fstat(descriptor).st_size > max_bytes:
            raise MaterializationError(f"{label} exceeds {max_bytes_label}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise MaterializationError(f"{label} exceeds {max_bytes_label}")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _build_manifest(
    *,
    root: Path,
    candidate_id: str,
    base_snapshot_id: str,
    code_spec_sha256: str,
    bundle_hash: str,
) -> CodeWorkspaceManifest:
    inventory = _inventory_workspace(root)
    workspace_id = _derive_workspace_id(
        candidate_id=candidate_id,
        base_snapshot_id=base_snapshot_id,
        code_spec_sha256=code_spec_sha256,
        bundle_hash=bundle_hash,
        root_mode=inventory.root_mode,
        directories=inventory.directories,
        files=inventory.files,
    )
    return CodeWorkspaceManifest(
        workspace_id=workspace_id,
        candidate_id=candidate_id,
        base_snapshot_id=base_snapshot_id,
        code_spec_sha256=code_spec_sha256,
        bundle_sha256=bundle_hash,
        root_mode=inventory.root_mode,
        directories=inventory.directories,
        files=inventory.files,
    )


def _inventory_workspace(root: Path) -> _WorkspaceInventory:
    if stat.S_IMODE(root.stat(follow_symlinks=False).st_mode) != 0o755:
        raise MaterializationError("workspace root mode must be 0755")
    directories: list[CodeWorkspaceDirectory] = []
    files: list[CodeWorkspaceFile] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        mode = path.stat(follow_symlinks=False).st_mode
        if stat.S_ISLNK(mode):
            raise MaterializationError(f"workspace contains a symlink: {relative}")
        if stat.S_ISDIR(mode):
            if _hard_forbidden(relative):
                raise MaterializationError(
                    f"workspace contains a hard-forbidden directory: {relative}"
                )
            if stat.S_IMODE(mode) != 0o755:
                raise MaterializationError(
                    f"workspace directory mode must be 0755: {relative}"
                )
            directories.append(CodeWorkspaceDirectory(path=relative))
            continue
        if not stat.S_ISREG(mode):
            raise MaterializationError(f"workspace contains a non-regular file: {relative}")
        if relative == _WORKSPACE_MANIFEST:
            continue
        if _hard_forbidden(relative):
            raise MaterializationError(
                f"workspace contains a hard-forbidden file: {relative}"
            )
        if stat.S_IMODE(mode) != 0o644:
            raise MaterializationError(f"workspace file mode must be 0644: {relative}")
        files.append(
            CodeWorkspaceFile(
                path=relative,
                sha256=_sha256_file(path),
                size_bytes=path.stat(follow_symlinks=False).st_size,
            )
        )
    if not files:
        raise MaterializationError("workspace must contain at least one published file")
    return _WorkspaceInventory(
        root_mode="0755",
        directories=tuple(directories),
        files=tuple(files),
    )


def _derive_workspace_id(
    *,
    candidate_id: str,
    base_snapshot_id: str,
    code_spec_sha256: str,
    bundle_hash: str,
    root_mode: Literal["0755"],
    directories: tuple[CodeWorkspaceDirectory, ...],
    files: tuple[CodeWorkspaceFile, ...],
) -> str:
    identity = {
        "candidate_id": candidate_id,
        "base_snapshot_id": base_snapshot_id,
        "code_spec_sha256": code_spec_sha256,
        "bundle_sha256": bundle_hash,
        "root_mode": root_mode,
        "directories": [
            item.model_dump(mode="json") for item in directories
        ],
        "files": [item.model_dump(mode="json") for item in files],
    }
    return f"codews_{stable_hash(identity, prefix='')[:24]}"


def _normalize_policy_paths(patterns: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if not patterns:
        raise MaterializationError(f"{label} must not be empty")
    normalized: list[str] = []
    for raw in patterns:
        stripped = raw.strip().replace("\\", "/")
        if not stripped:
            raise MaterializationError(f"{label} must not contain empty entries")
        if any(token in stripped for token in _GLOB_TOKENS):
            raise MaterializationError(f"{label} must not contain wildcard patterns")
        path = _safe_relative_path(stripped.rstrip("/"))
        if _hard_forbidden(path):
            raise MaterializationError(f"{label} contains a hard-forbidden path: {path}")
        normalized.append(path)
    if len(normalized) != len(set(normalized)):
        raise MaterializationError(f"{label} entries must be unique")
    return tuple(normalized)


def _normalize_protected_paths(patterns: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in patterns:
        file_part = raw.partition(":")[0]
        if not file_part.strip():
            raise MaterializationError("protected_paths contains an empty file path")
        normalized.extend(
            _normalize_policy_paths((file_part,), label="protected_paths")
        )
    if len(normalized) != len(set(normalized)):
        raise MaterializationError("protected_paths entries must be unique")
    return tuple(normalized)


def _matches_explicit(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def _safe_relative_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        not normalized
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\x00" in normalized
    ):
        raise ValueError("path must be a safe relative POSIX path")
    return pure.as_posix()


def _hard_forbidden(path: str) -> bool:
    pure = PurePosixPath(path)
    return (
        any(part.casefold() in _HARD_FORBIDDEN_PARTS for part in pure.parts)
        or pure.name.casefold() in _HARD_FORBIDDEN_NAMES
        or pure.suffix.casefold() in _HARD_FORBIDDEN_SUFFIXES
    )


def _directory_root(path: Path, *, create: bool, label: str) -> Path:
    absolute = _absolute_path(path)
    _assert_no_symlink_components(absolute, label=label)
    if create:
        absolute.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_components(absolute, label=label)
    return _existing_directory(absolute, label=label)


def _existing_directory(path: Path, *, label: str) -> Path:
    absolute = _absolute_path(path)
    _assert_no_symlink_components(absolute, label=label)
    try:
        mode = absolute.stat(follow_symlinks=False).st_mode
    except OSError as exc:
        raise MaterializationError(f"{label} must be an existing directory: {exc}") from exc
    if not stat.S_ISDIR(mode):
        raise MaterializationError(f"{label} must be an existing non-symlink directory")
    resolved = absolute.resolve(strict=True)
    _assert_no_symlink_components(resolved, label=label)
    return resolved


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _assert_no_symlink_components(path: Path, *, label: str) -> None:
    absolute = _absolute_path(path)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            mode = current.stat(follow_symlinks=False).st_mode
        except FileNotFoundError:
            return
        except OSError as exc:
            raise MaterializationError(f"cannot inspect {label} ancestors: {exc}") from exc
        if stat.S_ISLNK(mode):
            raise MaterializationError(f"{label} contains a symlink ancestor: {current}")


def _assert_contained(root: Path, target: Path, *, label: str) -> None:
    absolute_root = _absolute_path(root)
    absolute_target = _absolute_path(target)
    try:
        common = Path(os.path.commonpath((absolute_root, absolute_target)))
    except ValueError as exc:
        raise MaterializationError(f"{label} escapes its root") from exc
    if common != absolute_root:
        raise MaterializationError(f"{label} escapes its root")


def _path_exists(path: Path) -> bool:
    try:
        path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise MaterializationError(f"cannot inspect path existence: {exc}") from exc
    return True


def _fix_published_modes(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        mode = path.stat(follow_symlinks=False).st_mode
        if stat.S_ISLNK(mode):
            raise MaterializationError(f"workspace contains a symlink: {path}")
        if stat.S_ISDIR(mode):
            path.chmod(0o755)
        elif stat.S_ISREG(mode):
            path.chmod(0o644)
        else:
            raise MaterializationError(f"workspace contains a non-regular path: {path}")
    root.chmod(0o755)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MaterializationError(f"cannot hash workspace file: {path}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise MaterializationError(f"workspace file is not regular: {path}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return f"sha256:{digest.hexdigest()}"


def _check_expected(label: str, actual: str, expected: str | None) -> None:
    if expected is not None and actual != expected:
        raise MaterializationError(f"workspace {label} mismatch")
