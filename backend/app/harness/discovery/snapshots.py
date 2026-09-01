"""Content-addressed, read-only source snapshots for isolated candidates."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.harness.discovery.canonical import canonical_json, stable_hash

_SNAPSHOT_MANIFEST = "snapshot_manifest.json"
_WORKSPACE_MARKER = ".mars_candidate_workspace.json"
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_HARD_FORBIDDEN_PARTS = frozenset({".git", ".hg", ".svn", "__pycache__"})
_HARD_FORBIDDEN_NAMES = frozenset({".env", ".env.local", ".env.production"})
_HARD_FORBIDDEN_SUFFIXES = frozenset(
    {".ckpt", ".key", ".mat", ".npy", ".npz", ".pem", ".pkl", ".pt", ".pth"}
)


class SnapshotFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    executable: bool = False

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = value.strip().replace("\\", "/")
        pure = PurePosixPath(normalized)
        if (
            not normalized
            or pure.is_absolute()
            or "\x00" in normalized
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise ValueError("snapshot path must be a safe relative POSIX path")
        return pure.as_posix()


class SnapshotPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_paths: tuple[str, ...] = Field(min_length=1)
    forbidden_paths: tuple[str, ...] = ()
    ignore_patterns: tuple[str, ...] = ()
    max_file_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    max_files: int = Field(default=20_000, ge=1, le=100_000)
    max_total_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1,
        le=2 * 1024 * 1024 * 1024,
    )

    @field_validator("allowed_paths")
    @classmethod
    def validate_allowed_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("allowed_paths must not contain empty entries")
        return value


class SnapshotManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["snapshot_manifest.v1"] = "snapshot_manifest.v1"
    snapshot_id: str = Field(pattern=r"^snap_[0-9a-f]{24}$")
    project: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    policy_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    files: tuple[SnapshotFile, ...] = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_unique_sorted_paths(self) -> SnapshotManifest:
        paths = tuple(item.path for item in self.files)
        if paths != tuple(sorted(paths)):
            raise ValueError("snapshot files must be sorted by path")
        if len(paths) != len(set(paths)):
            raise ValueError("snapshot file paths must be unique")
        return self


@dataclass(frozen=True)
class SnapshotHandle:
    root: Path
    manifest: SnapshotManifest


class SnapshotError(ValueError):
    """Raised when a source tree cannot become a safe immutable snapshot."""


def create_snapshot(
    *,
    source_root: Path,
    cache_root: Path,
    project: str,
    source_ref: str,
    policy: SnapshotPolicy,
) -> SnapshotHandle:
    """Copy allow-listed regular files into a content-addressed read-only tree."""
    resolved_source = source_root.resolve()
    if source_root.is_symlink() or not resolved_source.is_dir():
        raise SnapshotError("source_root must be an existing non-symlink directory")
    if not project.strip() or not source_ref.strip():
        raise SnapshotError("project and source_ref must not be empty")

    selected = _select_files(resolved_source, policy)
    policy_hash = stable_hash(policy.model_dump(mode="json"))
    snapshot_id = _derive_snapshot_id(
        project=project,
        source_ref=source_ref,
        policy_hash=policy_hash,
        files=tuple(item for item, _path in selected),
    )
    manifest = SnapshotManifest(
        snapshot_id=snapshot_id,
        project=project,
        source_ref=source_ref,
        policy_hash=policy_hash,
        files=tuple(item for item, _path in selected),
    )

    cache_root.mkdir(parents=True, exist_ok=True)
    destination = cache_root / snapshot_id
    if destination.exists():
        existing = verify_snapshot(destination)
        if existing.manifest.snapshot_id != snapshot_id:
            raise SnapshotError("snapshot cache identity mismatch")
        return existing

    temporary = Path(tempfile.mkdtemp(prefix=".mars-snapshot-", dir=cache_root))
    try:
        for item, source in selected:
            target = temporary / item.path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o555 if item.executable else 0o444)
        _write_json(temporary / _SNAPSHOT_MANIFEST, manifest.model_dump(mode="json"))
        (temporary / _SNAPSHOT_MANIFEST).chmod(0o444)
        _make_directories_read_only(temporary)
        try:
            os.replace(temporary, destination)
        except FileExistsError:
            shutil.rmtree(temporary)
        return verify_snapshot(destination)
    except Exception:
        if temporary.exists():
            _make_tree_writable(temporary)
            shutil.rmtree(temporary)
        raise


def verify_snapshot(root: Path) -> SnapshotHandle:
    """Re-hash a snapshot and reject missing, changed, symlinked, or extra files."""
    resolved = root.resolve()
    if root.is_symlink() or not resolved.is_dir():
        raise SnapshotError("snapshot root must be an existing non-symlink directory")
    manifest_path = resolved / _SNAPSHOT_MANIFEST
    try:
        manifest_mode = manifest_path.stat(follow_symlinks=False).st_mode
        if not stat.S_ISREG(manifest_mode) or manifest_path.is_symlink():
            raise SnapshotError("snapshot manifest must be a regular file")
        manifest = SnapshotManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise SnapshotError(f"cannot read snapshot manifest: {exc}") from exc
    expected_snapshot_id = _derive_snapshot_id(
        project=manifest.project,
        source_ref=manifest.source_ref,
        policy_hash=manifest.policy_hash,
        files=manifest.files,
    )
    if manifest.snapshot_id != expected_snapshot_id or resolved.name != manifest.snapshot_id:
        raise SnapshotError("snapshot manifest identity mismatch")

    expected_paths = {item.path for item in manifest.files}
    expected_directories = {
        parent.as_posix()
        for item in manifest.files
        for parent in PurePosixPath(item.path).parents
        if parent.as_posix() != "."
    }
    actual_paths: set[str] = set()
    actual_directories: set[str] = set()
    for path in sorted(resolved.rglob("*")):
        rel = path.relative_to(resolved).as_posix()
        mode = path.stat(follow_symlinks=False).st_mode
        if stat.S_ISLNK(mode):
            raise SnapshotError(f"snapshot contains a symlink: {rel}")
        if stat.S_ISDIR(mode):
            if _hard_forbidden(rel):
                raise SnapshotError(f"snapshot contains a forbidden directory: {rel}")
            if stat.S_IMODE(mode) != 0o555:
                raise SnapshotError(f"snapshot directory mode mismatch: {rel}")
            actual_directories.add(rel)
            continue
        if rel == _SNAPSHOT_MANIFEST:
            continue
        if not stat.S_ISREG(mode):
            raise SnapshotError(f"snapshot contains a non-regular file: {rel}")
        if _hard_forbidden(rel):
            raise SnapshotError(f"snapshot contains a forbidden file: {rel}")
        actual_paths.add(rel)
    if actual_paths != expected_paths:
        raise SnapshotError("snapshot file set does not match its manifest")
    if actual_directories != expected_directories:
        raise SnapshotError("snapshot directory set does not match its manifest")

    for item in manifest.files:
        path = resolved / item.path
        if path.stat().st_size != item.size_bytes or _sha256_file(path) != item.sha256:
            raise SnapshotError(f"snapshot file hash mismatch: {item.path}")
        expected_mode = 0o555 if item.executable else 0o444
        if stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) != expected_mode:
            raise SnapshotError(f"snapshot file mode mismatch: {item.path}")
    if stat.S_IMODE(manifest_mode) != 0o444:
        raise SnapshotError("snapshot manifest mode mismatch")
    if stat.S_IMODE(resolved.stat(follow_symlinks=False).st_mode) != 0o555:
        raise SnapshotError("snapshot root mode mismatch")
    return SnapshotHandle(root=resolved, manifest=manifest)


def materialize_candidate_workspace(
    *,
    snapshot_root: Path,
    workspaces_root: Path,
    candidate_id: str,
) -> Path:
    """Create one writable candidate copy without ever hard-linking the baseline."""
    if _SAFE_IDENTIFIER.fullmatch(candidate_id) is None:
        raise SnapshotError("candidate_id contains unsafe characters")
    snapshot = verify_snapshot(snapshot_root)
    workspaces_root.mkdir(parents=True, exist_ok=True)
    destination = workspaces_root / candidate_id
    if destination.exists():
        marker = _read_workspace_marker(destination)
        if (
            marker.get("candidate_id") != candidate_id
            or marker.get("snapshot_id") != snapshot.manifest.snapshot_id
        ):
            raise SnapshotError("existing candidate workspace uses another snapshot")
        return destination.resolve()

    temporary = Path(tempfile.mkdtemp(prefix=".mars-candidate-", dir=workspaces_root))
    try:
        for item in snapshot.manifest.files:
            source = snapshot.root / item.path
            target = temporary / item.path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o755 if item.executable else 0o644)
        marker_payload = {
            "schema_id": "candidate_workspace.v1",
            "candidate_id": candidate_id,
            "snapshot_id": snapshot.manifest.snapshot_id,
        }
        _write_json(temporary / _WORKSPACE_MARKER, marker_payload)
        try:
            os.replace(temporary, destination)
        except FileExistsError:
            shutil.rmtree(temporary)
        marker = _read_workspace_marker(destination)
        if (
            marker.get("candidate_id") != candidate_id
            or marker.get("snapshot_id") != snapshot.manifest.snapshot_id
        ):
            raise SnapshotError("candidate workspace creation raced with another snapshot")
        return destination.resolve()
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _select_files(
    source_root: Path,
    policy: SnapshotPolicy,
) -> tuple[tuple[SnapshotFile, Path], ...]:
    selected: list[tuple[SnapshotFile, Path]] = []
    total_bytes = 0
    for path in sorted(source_root.rglob("*")):
        rel = path.relative_to(source_root).as_posix()
        if path.is_symlink():
            if _matches(rel, policy.allowed_paths):
                raise SnapshotError(f"allowed source path is a symlink: {rel}")
            continue
        if path.is_dir():
            continue
        mode = path.stat(follow_symlinks=False).st_mode
        if not stat.S_ISREG(mode):
            if _matches(rel, policy.allowed_paths):
                raise SnapshotError(f"allowed source path is not regular: {rel}")
            continue
        if not _matches(rel, policy.allowed_paths):
            continue
        if _hard_forbidden(rel) or _matches(rel, policy.forbidden_paths):
            raise SnapshotError(f"allowed source path is forbidden: {rel}")
        if _matches(rel, policy.ignore_patterns):
            continue
        size = path.stat().st_size
        if size > policy.max_file_bytes:
            raise SnapshotError(f"source file exceeds max_file_bytes: {rel}")
        if len(selected) >= policy.max_files:
            raise SnapshotError("snapshot exceeds max_files")
        total_bytes += size
        if total_bytes > policy.max_total_bytes:
            raise SnapshotError("snapshot exceeds max_total_bytes")
        selected.append(
            (
                SnapshotFile(
                    path=rel,
                    sha256=_sha256_file(path),
                    size_bytes=size,
                    executable=bool(mode & stat.S_IXUSR),
                ),
                path,
            )
        )
    if not selected:
        raise SnapshotError("snapshot policy selected no files")
    return tuple(selected)


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    for raw in patterns:
        pattern = raw.strip().replace("\\", "/")
        if not pattern:
            return True
        prefix = pattern.rstrip("/")
        if fnmatchcase(path, pattern) or path == prefix:
            return True
        if not any(token in pattern for token in "*?[") and path.startswith(prefix + "/"):
            return True
    return False


def _hard_forbidden(path: str) -> bool:
    pure = PurePosixPath(path)
    parts = tuple(part.casefold() for part in pure.parts)
    name = pure.name.casefold()
    return (
        any(part in _HARD_FORBIDDEN_PARTS for part in parts)
        or name in _HARD_FORBIDDEN_NAMES
        or pure.suffix.casefold() in _HARD_FORBIDDEN_SUFFIXES
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _derive_snapshot_id(
    *,
    project: str,
    source_ref: str,
    policy_hash: str,
    files: tuple[SnapshotFile, ...],
) -> str:
    identity = {
        "project": project,
        "source_ref": source_ref,
        "policy_hash": policy_hash,
        "files": [item.model_dump(mode="json") for item in files],
    }
    return f"snap_{stable_hash(identity, prefix='')[:24]}"


def _write_json(path: Path, payload: object) -> None:
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")


def _read_workspace_marker(root: Path) -> dict[str, object]:
    try:
        raw = json.loads((root / _WORKSPACE_MARKER).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"candidate workspace marker is unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise SnapshotError("candidate workspace marker must be an object")
    return {str(key): value for key, value in raw.items()}


def _make_directories_read_only(root: Path) -> None:
    for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
        path.chmod(0o555)
    root.chmod(0o555)


def _make_tree_writable(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif not path.is_symlink():
            path.chmod(0o644)
    root.chmod(0o755)
