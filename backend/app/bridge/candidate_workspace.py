"""Bridge-owned composition of project repos and isolated candidate snapshots."""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.harness.discovery.candidate_builder import genome_fingerprint
from app.harness.discovery.canonical import stable_hash
from app.harness.discovery.code_candidate import (
    CodeCandidateSpec,
    code_candidate_implementation_fingerprint,
    code_candidate_spec_sha256,
)
from app.harness.discovery.code_materialization import (
    CodeMaterializationBundle,
    MaterializationError,
    bundle_sha256,
    content_blob_path,
    content_sha256,
    materialize_code_workspace,
)
from app.harness.discovery.models import CandidateRecord, ResearchTaskContract
from app.harness.discovery.preflight import run_code_candidate_preflight

from app.harness.discovery.snapshots import (
    SnapshotHandle,
    SnapshotPolicy,
    create_snapshot,
    materialize_candidate_workspace,
)
from app.harness.tools.project_repo import ProjectRepo, load_project_repo
from app.harness.tools.registry import ToolContext, ToolRegistry
from app.storage.discovery_common import atomic_write_json, read_json
from app.storage.run_store import RunHandle

_MAX_GATE_BLOB_BYTES = 16 * 1024 * 1024


class CandidateWorkspaceError(ValueError):
    """Raised when a secure candidate workspace cannot be proven trustworthy."""


class GateAuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: Literal["code.patch_generator"] = "code.patch_generator"
    path: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal["success"] = "success"
    dry_run: Literal[True] = True


class CandidateWorkspaceReceipt(BaseModel):
    """Replayable provenance receipt stored outside the mutable workspace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["candidate_workspace_receipt.v1"] = (
        "candidate_workspace_receipt.v1"
    )
    candidate_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    project: str = Field(min_length=1)
    candidate_record_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    implementation_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    snapshot_id: str = Field(pattern=r"^snap_[0-9a-f]{24}$")
    snapshot_manifest_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    code_spec_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    bundle_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    workspace_manifest_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    snapshot_ref: str = Field(min_length=1)
    blob_root_ref: str = Field(min_length=1)
    workspace_ref: str = Field(min_length=1)
    workspace_manifest_ref: str = Field(min_length=1)
    receipt_ref: str = Field(min_length=1)
    gate_audit: tuple[GateAuditEntry, ...] = Field(min_length=1)


@dataclass(frozen=True)
class CandidateWorkspace:
    candidate_id: str
    root: Path
    snapshot: SnapshotHandle
    snapshot_ref: str
    workspace_ref: str
    receipt_ref: str = ""
    receipt_sha256: str = ""
    bundle_sha256: str = ""
    workspace_manifest_sha256: str = ""
    receipt: CandidateWorkspaceReceipt | None = None


class CandidateWorkspaceManager:
    """Create candidate copies below one run while preserving the live repo."""

    def prepare(
        self,
        *,
        project: str,
        run_root: Path,
        candidate_id: str,
    ) -> CandidateWorkspace:
        repo = load_project_repo(project)
        return self.prepare_from_repo(
            repo=repo,
            run_root=run_root,
            candidate_id=candidate_id,
        )

    def prepare_from_repo(
        self,
        *,
        repo: ProjectRepo,
        run_root: Path,
        candidate_id: str,
    ) -> CandidateWorkspace:
        snapshot = create_snapshot(
            source_root=repo.root,
            cache_root=run_root / "discovery" / "source_snapshots",
            project=repo.project,
            source_ref=f"{repo.repo_mode}:{repo.project}",
            policy=SnapshotPolicy(
                allowed_paths=repo.allowed_paths,
                ignore_patterns=repo.ignore_patterns,
            ),
        )
        workspace = materialize_candidate_workspace(
            snapshot_root=snapshot.root,
            workspaces_root=run_root / "discovery" / "candidate_workspaces",
            candidate_id=candidate_id,
        )
        return CandidateWorkspace(
            candidate_id=candidate_id,
            root=workspace,
            snapshot=snapshot,
            snapshot_ref=snapshot.root.relative_to(run_root).as_posix(),
            workspace_ref=workspace.relative_to(run_root).as_posix(),
        )

    async def prepare_secure_from_repo(
        self,
        *,
        repo: ProjectRepo,
        run_root: Path,
        candidate: CandidateRecord,
        code_spec: CodeCandidateSpec,
        bundle: CodeMaterializationBundle,
        tool_registry: ToolRegistry,
    ) -> CandidateWorkspace:
        """Gate-audit and atomically materialize one code-backed candidate."""
        resolved_run_root = _validated_run_root(run_root)
        discovery_root = resolved_run_root / "discovery"
        snapshot = create_snapshot(
            source_root=repo.root,
            cache_root=discovery_root / "source_snapshots",
            project=repo.project,
            source_ref=f"{repo.repo_mode}:{repo.project}",
            policy=SnapshotPolicy(
                allowed_paths=repo.allowed_paths,
                ignore_patterns=repo.ignore_patterns,
            ),
        )
        spec_hash = code_candidate_spec_sha256(code_spec)
        materialization_hash = bundle_sha256(bundle)
        implementation_hash = _validate_secure_identity(
            candidate=candidate,
            code_spec=code_spec,
            bundle=bundle,
            snapshot=snapshot,
            spec_hash=spec_hash,
            materialization_hash=materialization_hash,
        )

        blob_root = discovery_root / "code_blobs"
        gate_audit = await _audit_operations(
            repo=repo,
            run_root=resolved_run_root,
            candidate=candidate,
            bundle=bundle,
            blob_root=blob_root,
            tool_registry=tool_registry,
        )
        _reject_protected_operations(
            bundle=bundle,
            protected_paths=repo.protected_paths,
        )
        try:
            materialized = materialize_code_workspace(
                snapshot_root=snapshot.root,
                blob_root=blob_root,
                workspaces_root=discovery_root / "candidate_workspaces",
                candidate_id=candidate.candidate_id,
                bundle=bundle,
                allowed_paths=repo.allowed_paths,
                expected_touched_paths=code_spec.touched_paths,
                expected_entrypoint=code_spec.entrypoint,
                protected_paths=repo.protected_paths,
            )
        except MaterializationError as exc:
            raise CandidateWorkspaceError(f"secure materialization failed: {exc}") from exc

        snapshot_ref = _relative_ref(resolved_run_root, snapshot.root)
        blob_root_ref = _relative_ref(resolved_run_root, blob_root)
        workspace_ref = _relative_ref(resolved_run_root, materialized.root)
        workspace_manifest_ref = _relative_ref(
            resolved_run_root,
            materialized.manifest_path,
        )
        receipt_path = (
            discovery_root / "candidate_receipts" / f"{candidate.candidate_id}.json"
        )
        receipt_ref = _relative_ref(resolved_run_root, receipt_path, require_exists=False)
        receipt = CandidateWorkspaceReceipt(
            candidate_id=candidate.candidate_id,
            run_id=candidate.run_id,
            project=repo.project,
            candidate_record_sha256=stable_hash(candidate.model_dump(mode="json")),
            implementation_sha256=implementation_hash,
            snapshot_id=snapshot.manifest.snapshot_id,
            snapshot_manifest_sha256=stable_hash(snapshot.manifest.model_dump(mode="json")),
            code_spec_sha256=spec_hash,
            bundle_sha256=materialization_hash,
            workspace_manifest_sha256=materialized.manifest_sha256,
            policy_hash=_policy_hash(repo),
            snapshot_ref=snapshot_ref,
            blob_root_ref=blob_root_ref,
            workspace_ref=workspace_ref,
            workspace_manifest_ref=workspace_manifest_ref,
            receipt_ref=receipt_ref,
            gate_audit=gate_audit,
        )
        stored_receipt = _persist_or_replay_receipt(receipt_path, receipt)
        return CandidateWorkspace(
            candidate_id=candidate.candidate_id,
            root=materialized.root,
            snapshot=snapshot,
            snapshot_ref=snapshot_ref,
            workspace_ref=workspace_ref,
            receipt_ref=receipt_ref,
            receipt_sha256=stable_hash(stored_receipt.model_dump(mode="json")),
            bundle_sha256=materialization_hash,
            workspace_manifest_sha256=materialized.manifest_sha256,
            receipt=stored_receipt,
        )


class SecureCandidateWorkspacePreparer:
    """Production adapter for Discovery's secure code-candidate boundary."""

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        manager: CandidateWorkspaceManager | None = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.manager = manager or CandidateWorkspaceManager()

    async def prepare(
        self,
        *,
        run: RunHandle,
        contract: ResearchTaskContract,
        candidate: CandidateRecord,
        code_spec: CodeCandidateSpec,
        bundle: CodeMaterializationBundle,
    ) -> CandidateWorkspace:
        repo = load_project_repo(contract.project)
        _validate_composition_scope(
            run=run,
            contract=contract,
            candidate=candidate,
            repo=repo,
        )
        prepared = await self.manager.prepare_secure_from_repo(
            repo=repo,
            run_root=run.root,
            candidate=candidate,
            code_spec=code_spec,
            bundle=bundle,
            tool_registry=self.tool_registry,
        )
        report = run_code_candidate_preflight(
            candidate=candidate,
            contract=contract,
            spec=code_spec,
            snapshot_root=prepared.snapshot.root,
            bundle=bundle,
            candidate_workspace=prepared.root,
            touched_paths=code_spec.touched_paths,
            artifact_metadata=candidate.metadata,
        )
        if not report.passed:
            blockers = "; ".join(
                f"{check.check_id}: {check.reason or 'failed'}"
                for check in report.blockers
            )
            raise CandidateWorkspaceError(
                f"strict code candidate preflight failed: {blockers}"
            )
        return prepared


def _validate_secure_identity(
    *,
    candidate: CandidateRecord,
    code_spec: CodeCandidateSpec,
    bundle: CodeMaterializationBundle,
    snapshot: SnapshotHandle,
    spec_hash: str,
    materialization_hash: str,
) -> str:
    if code_spec.base_snapshot_id != snapshot.manifest.snapshot_id:
        raise CandidateWorkspaceError("CodeCandidateSpec does not bind the created snapshot")
    if bundle.base_snapshot_id != snapshot.manifest.snapshot_id:
        raise CandidateWorkspaceError("materialization bundle does not bind the created snapshot")
    if bundle.code_spec_sha256 != spec_hash:
        raise CandidateWorkspaceError("materialization bundle does not bind CodeCandidateSpec")
    operation_paths = tuple(operation.path for operation in bundle.operations)
    if operation_paths != tuple(sorted(code_spec.touched_paths)):
        raise CandidateWorkspaceError(
            "materialization operations differ from CodeCandidateSpec touched_paths"
        )
    if code_spec.entrypoint not in operation_paths:
        raise CandidateWorkspaceError("CodeCandidateSpec entrypoint is not materialized")
    exact_hash = genome_fingerprint(candidate.genome)
    if candidate.fingerprints.get("exact") != exact_hash:
        raise CandidateWorkspaceError("candidate exact genome fingerprint mismatch")
    expected = code_candidate_implementation_fingerprint(
        genome_exact_sha256=exact_hash,
        bundle_hash=materialization_hash,
    )
    if candidate.fingerprints.get("implementation") != expected:
        raise CandidateWorkspaceError("candidate implementation fingerprint mismatch")
    return expected


def _validate_composition_scope(
    *,
    run: RunHandle,
    contract: ResearchTaskContract,
    candidate: CandidateRecord,
    repo: ProjectRepo,
) -> None:
    if run.run_id != contract.run_id or candidate.run_id != contract.run_id:
        raise CandidateWorkspaceError(
            "run, contract, and candidate must share one run_id"
        )
    if run.project != contract.project or repo.project != contract.project:
        raise CandidateWorkspaceError(
            "run, contract, and repository must share one project"
        )

    repo_allowed = _normalize_policy_prefixes(
        repo.allowed_paths,
        label="repository allowed_paths",
    )
    contract_allowed = _normalize_policy_prefixes(
        contract.allowed_paths,
        label="contract allowed_paths",
    )
    contract_forbidden = _normalize_policy_prefixes(
        contract.forbidden_paths,
        label="contract forbidden_paths",
        allow_empty=True,
    )
    repo_protected = _normalize_policy_prefixes(
        tuple(value.partition(":")[0] for value in repo.protected_paths),
        label="repository protected_paths",
        allow_empty=True,
    )

    for allowed in contract_allowed:
        if not _covered_by(allowed, repo_allowed):
            raise CandidateWorkspaceError(
                f"contract allowed path expands repository policy: {allowed}"
            )
    for protected in repo_protected:
        for allowed in contract_allowed:
            overlap = _prefix_overlap(allowed, protected)
            if overlap is not None and not _covered_by(overlap, contract_forbidden):
                raise CandidateWorkspaceError(
                    "contract forbidden_paths do not cover repository protected path: "
                    f"{protected}"
                )


def _normalize_policy_prefixes(
    values: tuple[str, ...],
    *,
    label: str,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not values:
        if allow_empty:
            return ()
        raise CandidateWorkspaceError(f"{label} must not be empty")
    normalized: list[str] = []
    for raw in values:
        value = raw.strip().replace("\\", "/").rstrip("/")
        pure = PurePosixPath(value)
        if (
            not value
            or pure.is_absolute()
            or "\x00" in value
            or any(part in {"", ".", ".."} for part in pure.parts)
            or any(token in value for token in "*?[")
        ):
            raise CandidateWorkspaceError(f"{label} contains an unsafe path")
        normalized.append(pure.as_posix())
    if len(normalized) != len(set(normalized)):
        raise CandidateWorkspaceError(f"{label} contains duplicate paths")
    return tuple(normalized)


def _covered_by(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def _prefix_overlap(left: str, right: str) -> str | None:
    if left == right or left.startswith(right + "/"):
        return left
    if right.startswith(left + "/"):
        return right
    return None


def _reject_protected_operations(
    *,
    bundle: CodeMaterializationBundle,
    protected_paths: tuple[str, ...],
) -> None:
    """Fail before the materializer creates its workspace parent directory."""
    normalized: list[str] = []
    for raw in protected_paths:
        file_part = raw.partition(":")[0].strip().replace("\\", "/").rstrip("/")
        if not file_part:
            raise CandidateWorkspaceError(
                "secure materialization failed: protected_paths contains an empty file path"
            )
        normalized.append(file_part)
    for operation in bundle.operations:
        if any(
            operation.path == prefix or operation.path.startswith(prefix + "/")
            for prefix in normalized
        ):
            raise CandidateWorkspaceError(
                f"secure materialization failed: operation path is protected: "
                f"{operation.path}"
            )


async def _audit_operations(
    *,
    repo: ProjectRepo,
    run_root: Path,
    candidate: CandidateRecord,
    bundle: CodeMaterializationBundle,
    blob_root: Path,
    tool_registry: ToolRegistry,
) -> tuple[GateAuditEntry, ...]:
    entries: list[GateAuditEntry] = []
    context = ToolContext(
        run_id=candidate.run_id,
        project=repo.project,
        agent="coding",
        extra={"run_root": str(run_root)},
        project_repo_root=str(repo.root),
        dry_run=True,
    )
    for operation in bundle.operations:
        content = _read_cas_text(blob_root, operation.content_sha256)
        try:
            result = await tool_registry.dispatch(
                "code.patch_generator",
                {"path": operation.path, "content": content},
                context,
            )
        except Exception as exc:
            raise CandidateWorkspaceError(
                f"Gate 5 audit raised for {operation.path}: {exc}"
            ) from exc
        if (
            not result.ok
            or result.status != "success"
            or result.blocked_by_gate is not None
            or result.requires_approval
        ):
            reason = result.error or result.blocked_by_gate or result.status or "unknown"
            raise CandidateWorkspaceError(
                f"Gate 5 audit rejected {operation.path}: {reason}"
            )
        entries.append(
            GateAuditEntry(
                path=operation.path,
                content_sha256=operation.content_sha256,
            )
        )
    return tuple(entries)


def _read_cas_text(blob_root: Path, digest: str) -> str:
    try:
        resolved_root = blob_root.resolve(strict=True)
    except OSError as exc:
        raise CandidateWorkspaceError(f"code blob root is unavailable: {exc}") from exc
    if blob_root.is_symlink() or not resolved_root.is_dir():
        raise CandidateWorkspaceError("code blob root must be a non-symlink directory")
    path = content_blob_path(resolved_root, digest)
    current = resolved_root
    for part in path.relative_to(resolved_root).parts:
        current /= part
        if current.is_symlink():
            raise CandidateWorkspaceError("code blob path contains a symlink")
    try:
        path.resolve(strict=True).relative_to(resolved_root)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        descriptor = os.open(path, flags)
    except (OSError, ValueError) as exc:
        raise CandidateWorkspaceError(f"cannot open code blob: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise CandidateWorkspaceError("code blob must be a regular file")
        if metadata.st_size > _MAX_GATE_BLOB_BYTES:
            raise CandidateWorkspaceError("code blob exceeds Gate 5 audit limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_GATE_BLOB_BYTES:
                raise CandidateWorkspaceError("code blob exceeds Gate 5 audit limit")
            chunks.append(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    if content_sha256(payload) != digest:
        raise CandidateWorkspaceError("code blob content does not match its address")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CandidateWorkspaceError("code blob is not UTF-8 text") from exc
    if "\x00" in text:
        raise CandidateWorkspaceError("code blob contains NUL bytes")
    return text


def _validated_run_root(run_root: Path) -> Path:
    try:
        resolved = run_root.resolve(strict=True)
    except OSError as exc:
        raise CandidateWorkspaceError(f"run_root is unavailable: {exc}") from exc
    if run_root.is_symlink() or not resolved.is_dir():
        raise CandidateWorkspaceError("run_root must be an existing non-symlink directory")
    return resolved


def _relative_ref(root: Path, path: Path, *, require_exists: bool = True) -> str:
    try:
        resolved = path.resolve(strict=require_exists)
        return resolved.relative_to(root).as_posix()
    except (OSError, ValueError) as exc:
        raise CandidateWorkspaceError("candidate workspace artifact escapes run_root") from exc


def _policy_hash(repo: ProjectRepo) -> str:
    return stable_hash(
        {
            "schema_id": "candidate_workspace_policy.v1",
            "project": repo.project,
            "repo_mode": repo.repo_mode,
            "read_only": repo.read_only,
            "allowed_paths": repo.allowed_paths,
            "protected_paths": repo.protected_paths,
            "ignore_patterns": repo.ignore_patterns,
        }
    )


def _persist_or_replay_receipt(
    path: Path,
    expected: CandidateWorkspaceReceipt,
) -> CandidateWorkspaceReceipt:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise CandidateWorkspaceError("candidate receipt path contains a symlink")
    if path.exists():
        actual = _read_receipt(path)
        if actual != expected:
            raise CandidateWorkspaceError("existing candidate receipt does not match replay")
        return actual
    atomic_write_json(path, expected.model_dump(mode="json"))
    actual = _read_receipt(path)
    if actual != expected:
        raise CandidateWorkspaceError("persisted candidate receipt failed verification")
    return actual


def _read_receipt(path: Path) -> CandidateWorkspaceReceipt:
    try:
        return CandidateWorkspaceReceipt.model_validate(read_json(path))
    except (OSError, ValueError) as exc:
        raise CandidateWorkspaceError(f"candidate receipt is invalid: {exc}") from exc
