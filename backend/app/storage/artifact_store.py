"""Versioned artifact storage under ``runs/<id>/<agent>/``.

Versions follow ``<artifact>.v1.md`` / ``v2.md`` / ... / ``approved.md``
(per CLAUDE.md hard constraint #8).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger
from app.harness.persistence import atomic_write_json, atomic_write_text, path_lock

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
_STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ArtifactConflictError(ValueError):
    """An immutable artifact version was reused for different contents."""


class ArtifactCorruptionError(ValueError):
    """An approval commit no longer matches its immutable source artifact."""


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

    @property
    def lock_path(self) -> Path:
        return self.run.root / ".state-artifacts.lock"

    @staticmethod
    def _validate_stem(stem: str) -> None:
        if not _STEM_RE.fullmatch(stem) or ".." in stem:
            raise ValueError("invalid artifact stem")

    # -------------------------------------------------------------- discovery

    def _agent_dir(self, agent_dir: str) -> Path:
        if agent_dir not in RUN_SUBDIRS:
            raise ValueError(f"unknown agent dir '{agent_dir}'")
        return self.run.subdir(agent_dir)

    def list_versions(self, *, agent_dir: str, stem: str) -> list[ArtifactRef]:
        self._validate_stem(stem)
        with path_lock(self.lock_path):
            self._recover_approval(agent_dir=agent_dir, stem=stem)
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
        self._validate_stem(stem)
        with path_lock(self.lock_path):
            self._recover_approval(agent_dir=agent_dir, stem=stem)
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

        self._validate_stem(stem)
        if version is not None and not re.fullmatch(r"v[1-9]\d*", version):
            raise ValueError("write requires a numeric version; use approve for approved artifacts")
        with path_lock(self.lock_path):
            d = self._agent_dir(agent_dir)
            d.mkdir(exist_ok=True)
            ver = version or self._next_version(agent_dir=agent_dir, stem=stem)
            path = d / f"{stem}.{ver}.md"
            if path.exists():
                if path.read_bytes().decode("utf-8") != text:
                    raise ArtifactConflictError(f"immutable artifact already exists: {path.name}")
            else:
                atomic_write_text(path, text)
            ref = ArtifactRef(
                run_id=self.run.run_id, agent_dir=agent_dir, stem=stem, version=ver, path=path,
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
        """Commit an immutable approval receipt before publishing its pointer.

        The receipt is the commit point. A crash before the pointer update is
        repaired by ``latest``/``recover_approvals`` before graph recovery.
        """
        self._validate_stem(ref.stem)
        expected = self._agent_dir(ref.agent_dir) / ref.filename
        if ref.run_id != self.run.run_id or ref.path.resolve() != expected.resolve():
            raise ValueError("approval source is outside this run")
        if not re.fullmatch(r"v[1-9]\d*|approved", ref.version):
            raise ValueError("invalid approval source version")
        with path_lock(self.lock_path):
            self._recover_approval(agent_dir=ref.agent_dir, stem=ref.stem)
            text = expected.read_bytes().decode("utf-8")
            result = validate_document(text)
            if not result.valid:
                raise ArtifactValidationError(result)
            approved_path = expected.parent / f"{ref.stem}.approved.md"
            records = self._approval_dir(ref.agent_dir, ref.stem)
            existing = sorted(records.glob("*.json")) if records.exists() else []
            previous = json.loads(existing[-1].read_text(encoding="utf-8")) if existing else {}
            source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if ref.version != "approved" and (previous.get("source_version") != ref.version
                                               or previous.get("source_sha256") != source_hash):
                sequence = len(existing) + 1
                atomic_write_json(records / f"{sequence:020d}.json", {
                    "schema": "artifact_approval.v1", "run_id": self.run.run_id,
                    "agent_dir": ref.agent_dir, "stem": ref.stem, "sequence": sequence,
                    "source_version": ref.version, "source_sha256": source_hash,
                })
            atomic_write_text(approved_path, text)
            approved = ArtifactRef(run_id=ref.run_id, agent_dir=ref.agent_dir, stem=ref.stem,
                                   version="approved", path=approved_path)
            self._write_eval_reports(ref=approved, expected_schema=result.schema_id)
            return approved

    def _approval_dir(self, agent_dir: str, stem: str) -> Path:
        return self._agent_dir(agent_dir) / ".approvals" / stem

    def _recover_approval(self, *, agent_dir: str, stem: str) -> None:
        records = self._approval_dir(agent_dir, stem)
        paths = sorted(records.glob("*.json")) if records.exists() else []
        if not paths:
            return
        try:
            receipt = json.loads(paths[-1].read_text(encoding="utf-8"))
            if (receipt.get("run_id") != self.run.run_id or receipt.get("agent_dir") != agent_dir
                    or receipt.get("stem") != stem or receipt.get("sequence") != len(paths)
                    or paths[-1].name != f"{len(paths):020d}.json"
                    or not re.fullmatch(r"v[1-9]\d*", str(receipt.get("source_version")))):
                raise ValueError("invalid approval receipt")
            source = self._agent_dir(agent_dir) / f"{stem}.{receipt['source_version']}.md"
            text = source.read_bytes().decode("utf-8")
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != receipt.get("source_sha256"):
                raise ValueError("approval source hash mismatch")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise ArtifactCorruptionError(f"cannot recover approval for {agent_dir}/{stem}: {exc}") from exc
        target = source.parent / f"{stem}.approved.md"
        if not target.exists() or target.read_bytes().decode("utf-8") != text:
            atomic_write_text(target, text)
            ref = ArtifactRef(self.run.run_id, agent_dir, stem, "approved", target)
            result = validate_document(text)
            self._write_eval_reports(ref=ref, expected_schema=result.schema_id)

    def recover_approvals(self) -> None:
        """Repair committed approval pointers before exposing recovered state."""
        with path_lock(self.lock_path):
            for agent_dir in RUN_SUBDIRS:
                root = self._agent_dir(agent_dir) / ".approvals"
                if root.exists():
                    for directory in sorted(root.iterdir()):
                        if directory.is_dir():
                            self._validate_stem(directory.name)
                            self._recover_approval(agent_dir=agent_dir, stem=directory.name)

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
