"""Candidate checks performed before an execution is queued."""
from __future__ import annotations

import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from app.harness.discovery.candidate_builder import (
    ConfigDelta,
    derive_candidate_id,
    genome_fingerprint,
    validate_config_delta,
)
from app.harness.discovery.code_candidate import (
    CodeCandidateSpec,
    code_candidate_implementation_fingerprint,
    code_candidate_spec_sha256,
    inspect_code_candidate,
)
from app.harness.discovery.code_materialization import (
    CodeMaterializationBundle,
    MaterializationError,
    bundle_sha256,
    verify_code_workspace,
)
from app.harness.discovery.models import CandidateRecord, CandidateStatus, ResearchTaskContract
from app.harness.discovery.novelty import NoveltyDecision
from app.harness.discovery.snapshots import SnapshotError, SnapshotHandle, verify_snapshot


@dataclass(frozen=True)
class PreflightCheck:
    check_id: str
    passed: bool
    reason: str = ""


@dataclass(frozen=True)
class PreflightReport:
    passed: bool
    checks: tuple[PreflightCheck, ...]

    @property
    def blockers(self) -> tuple[PreflightCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)


@dataclass(frozen=True)
class PreflightPolicy:
    verify_stable_identity: bool = True
    require_exact_fingerprint: bool = True
    require_artifact_refs: tuple[str, ...] = ()


def run_preflight(
    *,
    candidate: CandidateRecord,
    contract: ResearchTaskContract,
    delta: ConfigDelta | None = None,
    touched_paths: tuple[str, ...] = (),
    novelty: NoveltyDecision | None = None,
    policy: PreflightPolicy | None = None,
) -> PreflightReport:
    cfg = policy or PreflightPolicy()
    checks: list[PreflightCheck] = []
    checks.append(
        PreflightCheck(
            "run_scope",
            candidate.run_id == contract.run_id,
            "candidate and contract run_id differ" if candidate.run_id != contract.run_id else "",
        )
    )
    checks.append(
        PreflightCheck(
            "candidate_state",
            candidate.status in {CandidateStatus.DRAFT, CandidateStatus.VALIDATED},
            f"candidate state '{candidate.status.value}' cannot enter preflight"
            if candidate.status not in {CandidateStatus.DRAFT, CandidateStatus.VALIDATED}
            else "",
        )
    )
    valid_lineage = candidate.candidate_id not in candidate.parent_ids and len(
        candidate.parent_ids
    ) == len(set(candidate.parent_ids))
    checks.append(
        PreflightCheck(
            "lineage",
            valid_lineage,
            "" if valid_lineage else "candidate lineage contains self or duplicate parents",
        )
    )
    if cfg.verify_stable_identity:
        try:
            expected_id = derive_candidate_id(
                run_id=candidate.run_id,
                genome=candidate.genome,
                parent_ids=candidate.parent_ids,
                generation=candidate.generation,
                iteration=candidate.iteration,
                creator=candidate.creator,
                operator=candidate.operator,
                implementation_fingerprint=candidate.fingerprints.get(
                    "implementation"
                ),
            )
            valid_identity = candidate.candidate_id == expected_id
            identity_reason = (
                ""
                if valid_identity
                else "candidate_id does not match deterministic content identity"
            )
        except ValueError as exc:
            valid_identity = False
            identity_reason = str(exc)
        checks.append(
            PreflightCheck(
                "stable_identity",
                valid_identity,
                identity_reason,
            )
        )
    if cfg.require_exact_fingerprint:
        expected_fingerprint = genome_fingerprint(candidate.genome)
        valid_fingerprint = candidate.fingerprints.get("exact") == expected_fingerprint
        checks.append(
            PreflightCheck(
                "exact_fingerprint",
                valid_fingerprint,
                "" if valid_fingerprint else "exact genome fingerprint is missing or inconsistent",
            )
        )
    for artifact_name in cfg.require_artifact_refs:
        present = bool(candidate.artifact_refs.get(artifact_name))
        checks.append(
            PreflightCheck(
                f"artifact:{artifact_name}",
                present,
                "" if present else f"required artifact ref '{artifact_name}' is missing",
            )
        )
    if delta is not None:
        delta_errors = validate_config_delta(
            candidate.genome,
            delta,
            allowed_zones=contract.evolution_zones,
        )
        checks.append(
            PreflightCheck(
                "config_delta",
                not delta_errors,
                "; ".join(delta_errors),
            )
        )
    for path in touched_paths:
        checks.append(_path_check(path, contract=contract))
    if novelty is not None:
        checks.append(
            PreflightCheck(
                "novelty",
                novelty.is_novel,
                (
                    f"duplicate of '{novelty.matching_candidate_id}' at "
                    f"{novelty.duplicate_kind.value if novelty.duplicate_kind else 'unknown'} layer"
                )
                if not novelty.is_novel
                else "",
            )
        )
    return PreflightReport(passed=all(check.passed for check in checks), checks=tuple(checks))


def run_code_candidate_preflight(
    *,
    candidate: CandidateRecord,
    contract: ResearchTaskContract,
    spec: CodeCandidateSpec,
    snapshot_root: Path,
    bundle: CodeMaterializationBundle,
    candidate_workspace: Path | None,
    artifact_metadata: Mapping[str, object] | None = None,
    delta: ConfigDelta | None = None,
    touched_paths: tuple[str, ...] = (),
    novelty: NoveltyDecision | None = None,
    policy: PreflightPolicy | None = None,
) -> PreflightReport:
    """Validate exact code provenance before reading candidate source as text.

    The function never imports the candidate entrypoint, executes source,
    applies a patch, or writes to either the snapshot or materialized workspace.
    A workspace is mandatory: baseline snapshot source is never a fallback.
    """

    base_report = run_preflight(
        candidate=candidate,
        contract=contract,
        delta=delta,
        touched_paths=touched_paths,
        novelty=novelty,
        policy=policy,
    )
    checks = list(base_report.checks)

    snapshot, snapshot_error = _verified_snapshot(snapshot_root)
    checks.append(
        PreflightCheck(
            "code_snapshot_integrity",
            snapshot is not None,
            snapshot_error,
        )
    )
    if snapshot is None:
        return _report(checks)

    snapshot_matches = snapshot.manifest.snapshot_id == spec.base_snapshot_id
    checks.append(
        PreflightCheck(
            "code_snapshot_identity",
            snapshot_matches,
            (
                "CodeCandidateSpec base_snapshot_id does not match snapshot manifest"
                if not snapshot_matches
                else ""
            ),
        )
    )

    bundle_snapshot_matches = bundle.base_snapshot_id == spec.base_snapshot_id
    checks.append(
        PreflightCheck(
            "code_bundle_snapshot",
            bundle_snapshot_matches,
            (
                "materialization bundle base_snapshot_id differs from CodeCandidateSpec"
                if not bundle_snapshot_matches
                else ""
            ),
        )
    )

    expected_spec_hash = code_candidate_spec_sha256(spec)
    bundle_spec_matches = bundle.code_spec_sha256 == expected_spec_hash
    checks.append(
        PreflightCheck(
            "code_bundle_spec",
            bundle_spec_matches,
            (
                "materialization bundle code_spec_sha256 differs from canonical CodeCandidateSpec"
                if not bundle_spec_matches
                else ""
            ),
        )
    )

    operation_paths = tuple(operation.path for operation in bundle.operations)
    expected_paths = tuple(sorted(spec.touched_paths))
    bundle_paths_match = operation_paths == expected_paths
    checks.append(
        PreflightCheck(
            "code_bundle_paths",
            bundle_paths_match,
            (
                "materialization bundle operation paths differ from CodeCandidateSpec touched_paths"
                if not bundle_paths_match
                else ""
            ),
        )
    )
    bundle_entrypoint_matches = spec.entrypoint in operation_paths
    checks.append(
        PreflightCheck(
            "code_bundle_entrypoint",
            bundle_entrypoint_matches,
            (
                "CodeCandidateSpec entrypoint is not materialized by the bundle"
                if not bundle_entrypoint_matches
                else ""
            ),
        )
    )

    metadata = artifact_metadata if artifact_metadata is not None else candidate.metadata
    declarations_match, declaration_reason = _touched_path_declarations_match(
        spec=spec,
        touched_paths=touched_paths,
        artifact_metadata=metadata,
    )
    checks.append(
        PreflightCheck(
            "code_touched_paths",
            declarations_match,
            declaration_reason,
        )
    )

    materialization_hash = bundle_sha256(bundle)
    expected_implementation = code_candidate_implementation_fingerprint(
        genome_exact_sha256=genome_fingerprint(candidate.genome),
        bundle_hash=materialization_hash,
    )
    implementation_matches = (
        candidate.fingerprints.get("implementation") == expected_implementation
    )
    checks.append(
        PreflightCheck(
            "code_implementation_fingerprint",
            implementation_matches,
            (
                "candidate implementation fingerprint does not bind its exact genome and bundle"
                if not implementation_matches
                else ""
            ),
        )
    )

    workspace_present = candidate_workspace is not None
    checks.append(
        PreflightCheck(
            "code_workspace_required",
            workspace_present,
            "a materialized candidate workspace is required" if not workspace_present else "",
        )
    )
    if candidate_workspace is None:
        return _report(checks)

    provenance_inputs_match = all(
        (
            snapshot_matches,
            bundle_snapshot_matches,
            bundle_spec_matches,
            bundle_paths_match,
            bundle_entrypoint_matches,
            declarations_match,
            implementation_matches,
        )
    )
    if not provenance_inputs_match:
        return _report(checks)

    try:
        workspace = verify_code_workspace(
            candidate_workspace,
            expected_candidate_id=candidate.candidate_id,
            expected_base_snapshot_id=spec.base_snapshot_id,
            expected_code_spec_sha256=expected_spec_hash,
            expected_bundle_sha256=materialization_hash,
            expected_snapshot_root=snapshot.root,
            expected_bundle=bundle,
            expected_touched_paths=spec.touched_paths,
            expected_entrypoint=spec.entrypoint,
        )
    except (MaterializationError, OSError) as exc:
        checks.append(
            PreflightCheck(
                "code_workspace_provenance",
                False,
                f"materialized workspace verification failed: {exc}",
            )
        )
        return _report(checks)
    checks.append(PreflightCheck("code_workspace_provenance", True))

    source, source_reason = _read_entrypoint_source(
        spec=spec,
        candidate_workspace=workspace.root,
    )
    checks.append(
        PreflightCheck(
            "code_entrypoint_source",
            source is not None,
            source_reason,
        )
    )
    if source is None:
        return _report(checks)

    try:
        code_report = inspect_code_candidate(
            spec,
            source=source,
            allowed_paths=contract.allowed_paths,
            forbidden_paths=contract.forbidden_paths,
        )
    except Exception as exc:
        checks.append(
            PreflightCheck(
                "code_candidate:inspection",
                False,
                f"code candidate inspection failed: {type(exc).__name__}",
            )
        )
        return _report(checks)
    checks.extend(
        PreflightCheck(
            f"code_candidate:{check.check_id}",
            check.passed,
            check.reason,
        )
        for check in code_report.checks
    )
    return _report(checks)


def _verified_snapshot(root: Path) -> tuple[SnapshotHandle | None, str]:
    try:
        return verify_snapshot(root), ""
    except (SnapshotError, OSError) as exc:
        return None, f"snapshot verification failed: {exc}"


def _touched_path_declarations_match(
    *,
    spec: CodeCandidateSpec,
    touched_paths: tuple[str, ...],
    artifact_metadata: Mapping[str, object],
) -> tuple[bool, str]:
    passed_paths, passed_error = _normalize_path_declaration(touched_paths)
    metadata_raw = artifact_metadata.get("touched_paths")
    if metadata_raw is None:
        nested = artifact_metadata.get("code_candidate")
        if isinstance(nested, Mapping):
            metadata_raw = nested.get("touched_paths")
    metadata_paths, metadata_error = _normalize_path_declaration(metadata_raw)
    expected = tuple(sorted(spec.touched_paths))

    reasons: list[str] = []
    if passed_error:
        reasons.append(f"touched_paths argument {passed_error}")
    elif passed_paths != expected:
        reasons.append("touched_paths argument differs from CodeCandidateSpec")
    if metadata_error:
        reasons.append(f"artifact metadata touched_paths {metadata_error}")
    elif metadata_paths != expected:
        reasons.append("artifact metadata touched_paths differs from CodeCandidateSpec")
    return not reasons, "; ".join(reasons)


def _normalize_path_declaration(raw: object) -> tuple[tuple[str, ...], str]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return (), "must be a sequence of paths"
    normalized: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            return (), "must contain only strings"
        path = value.strip().replace("\\", "/")
        pure = PurePosixPath(path)
        if (
            not path
            or pure.is_absolute()
            or "\x00" in path
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            return (), "contains an unsafe path"
        normalized.append(pure.as_posix())
    if len(normalized) != len(set(normalized)):
        return (), "contains duplicate paths"
    return tuple(sorted(normalized)), ""


def _read_entrypoint_source(
    *,
    spec: CodeCandidateSpec,
    candidate_workspace: Path,
) -> tuple[str | None, str]:
    return _read_regular_utf8_source(
        root=candidate_workspace,
        relative_path=spec.entrypoint,
        label="candidate workspace",
    )


def _read_regular_utf8_source(
    *,
    root: Path,
    relative_path: str,
    label: str,
) -> tuple[str | None, str]:
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        return None, f"{label} root is unavailable: {exc}"
    if root.is_symlink() or not resolved_root.is_dir():
        return None, f"{label} root must be a non-symlink directory"

    path = root
    for part in PurePosixPath(relative_path).parts:
        path = path / part
        if path.is_symlink():
            return None, f"{label} entrypoint path contains a symlink"
    try:
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(resolved_root)
        mode = resolved_path.stat(follow_symlinks=False).st_mode
        if not stat.S_ISREG(mode):
            return None, f"{label} entrypoint is not a regular file"
        return resolved_path.read_text(encoding="utf-8"), ""
    except UnicodeDecodeError:
        return None, f"{label} entrypoint is not valid UTF-8 source"
    except (OSError, ValueError) as exc:
        return None, f"{label} entrypoint cannot be read safely: {exc}"


def _report(checks: list[PreflightCheck]) -> PreflightReport:
    return PreflightReport(
        passed=all(check.passed for check in checks),
        checks=tuple(checks),
    )


def _path_check(path: str, *, contract: ResearchTaskContract) -> PreflightCheck:
    normalized = path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    safe = (
        bool(normalized)
        and not pure.is_absolute()
        and "\x00" not in normalized
        and all(part not in {"", ".", ".."} for part in pure.parts)
    )
    if not safe:
        return PreflightCheck(f"path:{path}", False, "path is absolute, empty, or traverses parents")
    if _matches_path(normalized, contract.forbidden_paths):
        return PreflightCheck(f"path:{path}", False, "path matches a forbidden pattern")
    if not contract.allowed_paths or not _matches_path(normalized, contract.allowed_paths):
        return PreflightCheck(f"path:{path}", False, "path is outside allowed patterns")
    return PreflightCheck(f"path:{path}", True)


def _matches_path(path: str, patterns: tuple[str, ...]) -> bool:
    for raw_pattern in patterns:
        pattern = raw_pattern.strip().replace("\\", "/")
        if not pattern:
            continue
        prefix = pattern.rstrip("/")
        if fnmatchcase(path, pattern) or path == prefix:
            return True
        if not any(token in pattern for token in "*?[") and path.startswith(prefix + "/"):
            return True
    return False
