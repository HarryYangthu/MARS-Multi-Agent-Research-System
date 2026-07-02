"""Versioned artifact storage under ``runs/<id>/<agent>/``.

Versions follow ``<artifact>.v1.md`` / ``v2.md`` / ... / ``approved.md``
(per CLAUDE.md hard constraint #8).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from app.harness.evaluation.artifacts import (
    write_reports_for_artifact,
)
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.validator import (
    ValidationResult,
    validate_document,
    validate_metadata,
)
from app.storage.run_store import RUN_SUBDIRS, RunHandle

# Map schema -> agent dir / artifact stem.
SCHEMA_TO_AGENT: dict[str, tuple[str, str]] = {
    "proposal.v1": ("idea", "idea_proposal"),
    "experiment_plan.v1": ("experiment", "experiment_plan"),
    "code_spec.v1": ("coding", "code_spec"),
    "run_log.v1": ("execution", "run_log"),
    "diagnosis.v1": ("diagnosis", "diagnosis"),
    "feedback_packet.v1": ("diagnosis", "feedback_packet"),
    "report.v1": ("writing", "research_report"),
    "report_bundle.v1": ("writing", "report_bundle"),
}

_VERSION_RE = re.compile(r"^(?P<stem>.+?)\.(?P<ver>v\d+|approved)\.md$")


@dataclass
class ArtifactRef:
    run_id: str
    agent_dir: str  # idea / experiment / coding / execution / writing
    stem: str  # e.g. idea_proposal
    version: str  # v1 / v2 / approved
    path: Path

    @property
    def filename(self) -> str:
        return f"{self.stem}.{self.version}.md"


class ArtifactValidationError(ValueError):
    def __init__(self, result: ValidationResult) -> None:
        msg = result.first_error() or "validation failed"
        super().__init__(msg)
        self.result = result


class ArtifactStore:
    def __init__(self, run: RunHandle) -> None:
        self.run = run

    # -------------------------------------------------------------- discovery

    def _agent_dir(self, agent_dir: str) -> Path:
        if agent_dir not in RUN_SUBDIRS:
            raise ValueError(f"unknown agent dir '{agent_dir}'")
        return self.run.subdir(agent_dir)

    def list_versions(self, *, agent_dir: str, stem: str) -> list[ArtifactRef]:
        d = self._agent_dir(agent_dir)
        if not d.exists():
            return []
        out: list[ArtifactRef] = []
        for p in d.iterdir():
            m = _VERSION_RE.match(p.name)
            if not m or m.group("stem") != stem:
                continue
            out.append(
                ArtifactRef(
                    run_id=self.run.run_id,
                    agent_dir=agent_dir,
                    stem=stem,
                    version=m.group("ver"),
                    path=p,
                )
            )
        # numeric versions ascending, approved last
        out.sort(key=lambda r: (r.version == "approved", _version_sort_key(r.version)))
        return out

    def latest(self, *, agent_dir: str, stem: str) -> ArtifactRef | None:
        versions = self.list_versions(agent_dir=agent_dir, stem=stem)
        if not versions:
            return None
        approved = [v for v in versions if v.version == "approved"]
        if approved:
            return approved[-1]
        return versions[-1]

    # ------------------------------------------------------------------ write

    def _next_version(
        self, *, agent_dir: str, stem: str
    ) -> str:
        versions = self.list_versions(agent_dir=agent_dir, stem=stem)
        max_v = 0
        for v in versions:
            if v.version.startswith("v"):
                try:
                    max_v = max(max_v, int(v.version[1:]))
                except ValueError:
                    continue
        return f"v{max_v + 1}"

    def write(
        self,
        *,
        text: str,
        agent_dir: str | None = None,
        stem: str | None = None,
        expected_schema: str | None = None,
        version: str | None = None,
    ) -> ArtifactRef:
        """Validate frontmatter and write to ``runs/<id>/<agent>/<stem>.<ver>.md``.

        If ``agent_dir`` / ``stem`` are not given, they are inferred from the
        schema id via SCHEMA_TO_AGENT.
        """
        result = validate_document(text, expected_schema=expected_schema)
        if not result.valid or result.schema_id is None:
            raise ArtifactValidationError(result)

        if agent_dir is None or stem is None:
            mapping = SCHEMA_TO_AGENT.get(result.schema_id)
            if mapping is None:
                raise ArtifactValidationError(result)
            inferred_dir, inferred_stem = mapping
            agent_dir = agent_dir or inferred_dir
            stem = stem or inferred_stem

        d = self._agent_dir(agent_dir)
        d.mkdir(exist_ok=True)
        ver = version or self._next_version(agent_dir=agent_dir, stem=stem)
        path = d / f"{stem}.{ver}.md"
        path.write_text(text, encoding="utf-8")
        ref = ArtifactRef(
            run_id=self.run.run_id,
            agent_dir=agent_dir,
            stem=stem,
            version=ver,
            path=path,
        )
        self._write_eval_reports(ref=ref, expected_schema=result.schema_id)
        return ref

    def write_metadata(
        self,
        *,
        metadata: dict[str, Any],
        body: str,
        agent_dir: str | None = None,
        stem: str | None = None,
        expected_schema: str | None = None,
        version: str | None = None,
    ) -> ArtifactRef:
        """Convenience: validate the metadata dict, then serialize and write."""
        result = validate_metadata(metadata, expected_schema=expected_schema)
        if not result.valid or result.schema_id is None:
            raise ArtifactValidationError(result)
        text = fm_dumps(metadata, body)
        return self.write(
            text=text,
            agent_dir=agent_dir,
            stem=stem,
            expected_schema=expected_schema,
            version=version,
        )

    def approve(self, ref: ArtifactRef) -> ArtifactRef:
        """Promote ``ref`` to ``<stem>.approved.md`` (copy contents)."""
        approved_path = ref.path.parent / f"{ref.stem}.approved.md"
        text = ref.path.read_text(encoding="utf-8")
        approved_path.write_text(text, encoding="utf-8")
        approved = ArtifactRef(
            run_id=ref.run_id,
            agent_dir=ref.agent_dir,
            stem=ref.stem,
            version="approved",
            path=approved_path,
        )
        result = validate_document(text)
        self._write_eval_reports(
            ref=approved,
            expected_schema=result.schema_id if result.valid else None,
        )
        return approved

    def write_eval_reports(self, ref: ArtifactRef, *, expected_schema: str | None) -> list[Path]:
        return self._write_eval_reports(ref=ref, expected_schema=expected_schema)

    def _write_eval_reports(self, *, ref: ArtifactRef, expected_schema: str | None) -> list[Path]:
        try:
            return write_reports_for_artifact(
                project=self.run.project,
                artifact_path=ref.path,
                run_root=self.run.root,
                stem=ref.stem,
                version=ref.version,
                expected_schema=expected_schema,
            )
        except Exception as exc:  # pragma: no cover - eval must not block artifact writes
            logger.warning(
                "artifact evaluation failed: run={} artifact={} error={}",
                self.run.run_id,
                ref.path.name,
                exc,
            )
            return []


def _version_sort_key(v: str) -> int:
    if v == "approved":
        return 10**9
    if v.startswith("v"):
        try:
            return int(v[1:])
        except ValueError:
            return 0
    return 0
