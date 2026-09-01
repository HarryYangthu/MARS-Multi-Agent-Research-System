"""Recover trusted code workspaces from durable discovery artifacts.

The resolver deliberately ignores all path-like values in ``AdapterRequest``.
Only a configured runs root, a persisted candidate record, and the external
candidate-workspace receipt may select local files.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

from app.bridge.candidate_workspace import CandidateWorkspaceReceipt
from app.execution.adapters.base import AdapterRequest
from app.harness.discovery.canonical import stable_hash
from app.harness.discovery.code_candidate import (
    CodeCandidateSpec,
    code_candidate_spec_sha256,
)
from app.harness.discovery.code_materialization import (
    CodeMaterializationBundle,
    bundle_sha256,
)
from app.harness.discovery.code_workspace_transfer import (
    CodeWorkspaceTransferPackage,
    build_code_workspace_transfer,
)
from app.harness.discovery.models import CandidateRecord
from app.storage.discovery_common import read_json, stable_key

_CODE_SPEC_REF = "code_candidate_spec"
_BUNDLE_REF = "code_materialization_bundle"
_WORKSPACE_MANIFEST = ".mars_code_workspace_manifest.json"
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class PersistedWorkspaceResolutionError(ValueError):
    """Durable workspace provenance is absent, ambiguous, or inconsistent."""


@dataclass(frozen=True)
class _LocatedCandidate:
    run_root: Path
    current: CandidateRecord
    records: tuple[CandidateRecord, ...]


@dataclass(frozen=True)
class PersistedCodeWorkspaceResolver:
    """Filesystem resolver that remains valid after process restart."""

    runs_root: Path

    async def resolve(
        self,
        request: AdapterRequest,
    ) -> CodeWorkspaceTransferPackage | None:
        if not request.candidate_id:
            return None
        return await asyncio.to_thread(self._resolve_sync, request)

    def _resolve_sync(
        self,
        request: AdapterRequest,
    ) -> CodeWorkspaceTransferPackage | None:
        if _SAFE_IDENTIFIER.fullmatch(request.candidate_id) is None:
            return None
        root = _trusted_runs_root(self.runs_root)
        located = _find_candidate(root, request.candidate_id)
        if located is None:
            return None
        run_root = located.run_root
        current = located.current
        spec_declared = _CODE_SPEC_REF in current.artifact_refs
        bundle_declared = _BUNDLE_REF in current.artifact_refs
        receipt_path = (
            run_root
            / "discovery"
            / "candidate_receipts"
            / f"{request.candidate_id}.json"
        )
        receipt_exists = receipt_path.exists() or receipt_path.is_symlink()
        if not spec_declared and not bundle_declared:
            if receipt_exists:
                raise PersistedWorkspaceResolutionError(
                    "config-only candidate unexpectedly has a code-workspace receipt"
                )
            return None
        if spec_declared != bundle_declared:
            raise PersistedWorkspaceResolutionError(
                "code candidate must persist both code spec and bundle artifact refs"
            )
        if not receipt_exists:
            raise PersistedWorkspaceResolutionError(
                "code candidate has no persisted workspace receipt"
            )

        receipt_file = _trusted_path(
            run_root,
            receipt_path.relative_to(run_root).as_posix(),
            label="candidate workspace receipt",
            kind="file",
        )
        receipt = _read_receipt(receipt_file)
        _validate_run_and_receipt(
            trusted_runs_root=root,
            run_root=run_root,
            request=request,
            receipt_path=receipt_file,
            receipt=receipt,
        )
        candidate = _candidate_bound_by_receipt(
            current=current,
            records=located.records,
            receipt=receipt,
        )
        snapshot_root = _trusted_path(
            run_root,
            receipt.snapshot_ref,
            label="snapshot_ref",
            kind="directory",
        )
        blob_root = _trusted_path(
            run_root,
            receipt.blob_root_ref,
            label="blob_root_ref",
            kind="directory",
        )
        workspace_root = _trusted_path(
            run_root,
            receipt.workspace_ref,
            label="workspace_ref",
            kind="directory",
        )
        workspace_manifest = _trusted_path(
            run_root,
            receipt.workspace_manifest_ref,
            label="workspace_manifest_ref",
            kind="file",
        )
        expected_manifest = workspace_root / _WORKSPACE_MANIFEST
        if workspace_manifest != expected_manifest:
            raise PersistedWorkspaceResolutionError(
                "workspace_manifest_ref does not name the workspace manifest"
            )
        # The blob root is part of the signed receipt even though packaging only
        # needs the already-materialized workspace.  Resolving it closes the
        # path trust boundary and detects incomplete restart state.
        if not blob_root.is_dir():  # pragma: no cover - guaranteed by _trusted_path
            raise PersistedWorkspaceResolutionError("blob_root_ref is unavailable")

        spec_path = _trusted_path(
            run_root,
            candidate.artifact_refs[_CODE_SPEC_REF],
            label="code_candidate_spec artifact",
            kind="file",
        )
        bundle_path = _trusted_path(
            run_root,
            candidate.artifact_refs[_BUNDLE_REF],
            label="code_materialization_bundle artifact",
            kind="file",
        )
        try:
            spec = CodeCandidateSpec.model_validate(read_json(spec_path))
            bundle = CodeMaterializationBundle.model_validate(read_json(bundle_path))
        except (OSError, TypeError, ValueError) as exc:
            raise PersistedWorkspaceResolutionError(
                f"persisted code candidate artifacts are invalid: {exc}"
            ) from exc
        _validate_code_artifacts(receipt=receipt, spec=spec, bundle=bundle)

        package = build_code_workspace_transfer(
            workspace_root=workspace_root,
            snapshot_root=snapshot_root,
            bundle=bundle,
            expected_touched_paths=spec.touched_paths,
            expected_entrypoint=spec.entrypoint,
            transfer_root=run_root / "discovery" / "code_workspace_transfers",
        )
        _validate_transfer_matches_receipt(package=package, receipt=receipt)
        return package


def _trusted_runs_root(path: Path) -> Path:
    expanded = path.expanduser()
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise PersistedWorkspaceResolutionError(
            f"trusted runs root is unavailable: {exc}"
        ) from exc
    if expanded.is_symlink() or not resolved.is_dir():
        raise PersistedWorkspaceResolutionError(
            "trusted runs root must be an existing non-symlink directory"
        )
    return resolved


def _find_candidate(
    runs_root: Path,
    candidate_id: str,
) -> _LocatedCandidate | None:
    filename = f"{stable_key(candidate_id)}.json"
    history_key = stable_key(candidate_id)
    matches: list[_LocatedCandidate] = []
    for run_root in sorted(runs_root.iterdir()):
        if run_root.name == ".trash" or run_root.is_symlink() or not run_root.is_dir():
            continue
        path = run_root / "discovery" / "candidates" / "current" / filename
        history_dir = (
            run_root / "discovery" / "candidates" / "history" / history_key
        )
        receipt_path = (
            run_root / "discovery" / "candidate_receipts" / f"{candidate_id}.json"
        )
        has_current = path.exists() or path.is_symlink()
        has_history = history_dir.exists() or history_dir.is_symlink()
        has_receipt = receipt_path.exists() or receipt_path.is_symlink()
        if not has_current and not has_history and not has_receipt:
            continue
        records: list[CandidateRecord] = []
        current: CandidateRecord | None = None
        if has_current:
            trusted = _trusted_path(
                run_root,
                path.relative_to(run_root).as_posix(),
                label="candidate record",
                kind="file",
            )
            current = _read_candidate(trusted)
            records.append(current)
        if has_history:
            trusted_history = _trusted_path(
                run_root,
                history_dir.relative_to(run_root).as_posix(),
                label="candidate history",
                kind="directory",
            )
            for history_path in sorted(trusted_history.glob("*.json")):
                trusted_entry = _trusted_path(
                    run_root,
                    history_path.relative_to(run_root).as_posix(),
                    label="candidate history record",
                    kind="file",
                )
                records.append(_read_candidate(trusted_entry))
        if not records:
            raise PersistedWorkspaceResolutionError(
                "candidate workspace receipt exists without a persisted candidate record"
            )
        for record in records:
            if record.candidate_id != candidate_id:
                raise PersistedWorkspaceResolutionError(
                    "candidate record filename and candidate_id disagree"
                )
        recovered_current = records[-1] if has_history else current
        if recovered_current is None:  # pragma: no cover - guarded by records
            raise PersistedWorkspaceResolutionError(
                "persisted candidate current record is unavailable"
            )
        matches.append(
            _LocatedCandidate(
                run_root=run_root.resolve(strict=True),
                current=recovered_current,
                records=tuple(records),
            )
        )
    if not matches:
        return None
    if len(matches) != 1:
        raise PersistedWorkspaceResolutionError(
            "candidate_id is ambiguous across the trusted runs root"
        )
    return matches[0]


def _validate_run_and_receipt(
    *,
    trusted_runs_root: Path,
    run_root: Path,
    request: AdapterRequest,
    receipt_path: Path,
    receipt: CandidateWorkspaceReceipt,
) -> None:
    parent_meta = _run_meta(run_root)
    if parent_meta.get("run_id") != run_root.name or receipt.run_id != run_root.name:
        raise PersistedWorkspaceResolutionError("parent run identity mismatch")
    if parent_meta.get("project") != receipt.project or request.project != receipt.project:
        raise PersistedWorkspaceResolutionError("workspace project identity mismatch")
    if receipt.candidate_id != request.candidate_id:
        raise PersistedWorkspaceResolutionError("workspace candidate identity mismatch")
    expected_receipt_ref = receipt_path.relative_to(run_root).as_posix()
    if receipt.receipt_ref != expected_receipt_ref:
        raise PersistedWorkspaceResolutionError(
            "candidate receipt does not bind its persisted path"
        )

    if request.run_id == receipt.run_id:
        return
    if _SAFE_IDENTIFIER.fullmatch(request.run_id) is None:
        raise PersistedWorkspaceResolutionError("adapter request run_id is invalid")
    child_root = _trusted_path(
        trusted_runs_root,
        request.run_id,
        label="adapter request run",
        kind="directory",
    )
    child_meta = _run_meta(child_root)
    expected_task_prefix = f"discovery_child__{receipt.run_id}__"
    if (
        child_meta.get("run_id") != request.run_id
        or child_meta.get("project") != receipt.project
        or not str(child_meta.get("task", "")).startswith(expected_task_prefix)
    ):
        raise PersistedWorkspaceResolutionError(
            "adapter request run is not a persisted child of the candidate run"
        )


def _candidate_bound_by_receipt(
    *,
    current: CandidateRecord,
    records: tuple[CandidateRecord, ...],
    receipt: CandidateWorkspaceReceipt,
) -> CandidateRecord:
    if current.run_id != receipt.run_id:
        raise PersistedWorkspaceResolutionError("candidate run_id mismatch")
    matches = {
        stable_hash(record.model_dump(mode="json")): record
        for record in records
        if stable_hash(record.model_dump(mode="json"))
        == receipt.candidate_record_sha256
    }
    if len(matches) != 1:
        raise PersistedWorkspaceResolutionError(
            "no unique persisted candidate record matches the workspace receipt"
        )
    candidate = next(iter(matches.values()))
    if current.artifact_refs != candidate.artifact_refs:
        raise PersistedWorkspaceResolutionError(
            "current candidate artifact refs differ from receipt-bound history"
        )
    if candidate.fingerprints.get("implementation") != receipt.implementation_sha256:
        raise PersistedWorkspaceResolutionError(
            "candidate implementation fingerprint differs from workspace receipt"
        )
    return candidate


def _read_candidate(path: Path) -> CandidateRecord:
    try:
        return CandidateRecord.model_validate(read_json(path))
    except (OSError, TypeError, ValueError) as exc:
        raise PersistedWorkspaceResolutionError(
            f"persisted candidate record is invalid: {exc}"
        ) from exc


def _validate_code_artifacts(
    *,
    receipt: CandidateWorkspaceReceipt,
    spec: CodeCandidateSpec,
    bundle: CodeMaterializationBundle,
) -> None:
    if code_candidate_spec_sha256(spec) != receipt.code_spec_sha256:
        raise PersistedWorkspaceResolutionError(
            "code spec hash differs from workspace receipt"
        )
    if bundle_sha256(bundle) != receipt.bundle_sha256:
        raise PersistedWorkspaceResolutionError(
            "materialization bundle hash differs from workspace receipt"
        )
    if spec.base_snapshot_id != receipt.snapshot_id:
        raise PersistedWorkspaceResolutionError(
            "code spec snapshot differs from workspace receipt"
        )
    if bundle.base_snapshot_id != receipt.snapshot_id:
        raise PersistedWorkspaceResolutionError(
            "materialization bundle snapshot differs from workspace receipt"
        )
    if bundle.code_spec_sha256 != receipt.code_spec_sha256:
        raise PersistedWorkspaceResolutionError(
            "materialization bundle does not bind the persisted code spec"
        )
    audited = tuple((entry.path, entry.content_sha256) for entry in receipt.gate_audit)
    operations = tuple(
        (operation.path, operation.content_sha256) for operation in bundle.operations
    )
    if audited != operations:
        raise PersistedWorkspaceResolutionError(
            "Gate 5 audit does not exactly cover the materialization bundle"
        )


def _validate_transfer_matches_receipt(
    *,
    package: CodeWorkspaceTransferPackage,
    receipt: CandidateWorkspaceReceipt,
) -> None:
    transfer = package.receipt
    expected = (
        ("candidate_id", transfer.candidate_id, receipt.candidate_id),
        ("snapshot_id", transfer.base_snapshot_id, receipt.snapshot_id),
        (
            "snapshot_manifest_sha256",
            transfer.snapshot_manifest_sha256,
            receipt.snapshot_manifest_sha256,
        ),
        ("code_spec_sha256", transfer.code_spec_sha256, receipt.code_spec_sha256),
        ("bundle_sha256", transfer.bundle_sha256, receipt.bundle_sha256),
        (
            "workspace_manifest_sha256",
            transfer.workspace_manifest_sha256,
            receipt.workspace_manifest_sha256,
        ),
    )
    for label, actual, persisted in expected:
        if actual != persisted:
            raise PersistedWorkspaceResolutionError(
                f"transfer {label} differs from candidate workspace receipt"
            )


def _trusted_path(
    root: Path,
    raw_ref: str,
    *,
    label: str,
    kind: str,
) -> Path:
    if "\\" in raw_ref or "\x00" in raw_ref:
        raise PersistedWorkspaceResolutionError(f"{label} is not normalized POSIX")
    pure = PurePosixPath(raw_ref)
    if (
        not raw_ref
        or pure.is_absolute()
        or raw_ref != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise PersistedWorkspaceResolutionError(f"{label} is not a safe relative path")
    normalized_root = root.resolve(strict=True)
    current = normalized_root
    for part in pure.parts:
        current /= part
        try:
            mode = current.stat(follow_symlinks=False).st_mode
        except OSError as exc:
            raise PersistedWorkspaceResolutionError(f"{label} is unavailable: {exc}") from exc
        if stat.S_ISLNK(mode):
            raise PersistedWorkspaceResolutionError(f"{label} contains a symbolic link")
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(normalized_root)
    except (OSError, ValueError) as exc:
        raise PersistedWorkspaceResolutionError(f"{label} escapes its trusted root") from exc
    mode = resolved.stat(follow_symlinks=False).st_mode
    if kind == "file" and not stat.S_ISREG(mode):
        raise PersistedWorkspaceResolutionError(f"{label} must be a regular file")
    if kind == "directory" and not stat.S_ISDIR(mode):
        raise PersistedWorkspaceResolutionError(f"{label} must be a directory")
    return resolved


def _read_receipt(path: Path) -> CandidateWorkspaceReceipt:
    try:
        return CandidateWorkspaceReceipt.model_validate(read_json(path))
    except (OSError, TypeError, ValueError) as exc:
        raise PersistedWorkspaceResolutionError(
            f"candidate workspace receipt is invalid: {exc}"
        ) from exc


def _run_meta(run_root: Path) -> dict[str, Any]:
    path = _trusted_path(
        run_root,
        "run_meta.json",
        label="run metadata",
        kind="file",
    )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PersistedWorkspaceResolutionError(f"run metadata is invalid: {exc}") from exc
    if not isinstance(raw, dict):
        raise PersistedWorkspaceResolutionError("run metadata must be a JSON object")
    return {str(key): value for key, value in raw.items()}
