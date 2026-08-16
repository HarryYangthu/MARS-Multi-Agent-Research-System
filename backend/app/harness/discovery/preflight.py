"""Filesystem-free candidate checks performed before an execution is queued."""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from app.harness.discovery.candidate_builder import (
    ConfigDelta,
    derive_candidate_id,
    genome_fingerprint,
    validate_config_delta,
)
from app.harness.discovery.models import CandidateRecord, CandidateStatus, ResearchTaskContract
from app.harness.discovery.novelty import NoveltyDecision


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
        expected_id = derive_candidate_id(
            run_id=candidate.run_id,
            genome=candidate.genome,
            parent_ids=candidate.parent_ids,
            generation=candidate.generation,
            iteration=candidate.iteration,
            creator=candidate.creator,
            operator=candidate.operator,
        )
        valid_identity = candidate.candidate_id == expected_id
        checks.append(
            PreflightCheck(
                "stable_identity",
                valid_identity,
                "" if valid_identity else "candidate_id does not match deterministic content identity",
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
