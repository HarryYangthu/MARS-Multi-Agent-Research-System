"""Apply human edits / comment-driven revisions to an artifact."""
from __future__ import annotations

from typing import Any

from app.harness.evaluation.artifacts import write_reports_for_artifact
from app.harness.persistence import atomic_write_text, path_lock
from app.harness.schema.frontmatter_parser import dumps as fm_dumps, parse as fm_parse
from app.harness.schema.validator import (
    ValidationResult,
    validate_metadata,
)
from app.storage.artifact_store import ArtifactRef, ArtifactStore


def apply_human_edit(
    *,
    art_store: ArtifactStore,
    base: ArtifactRef,
    body: str | None = None,
    metadata_patch: dict[str, Any] | None = None,
    expected_schema: str | None = None,
) -> tuple[ArtifactRef, ValidationResult]:
    """Persist a new ``vN`` version that combines the base with human edits.

    Returns the new ArtifactRef plus the schema validation result. If
    validation fails, the new version is still written (so the UI can show
    the errors), and the result.valid is False.
    """
    expected_path = art_store.run.root / base.agent_dir / base.filename
    if base.run_id != art_store.run.run_id or base.path.resolve() != expected_path.resolve():
        raise ValueError("human edit source is outside this run")
    text = base.path.read_text(encoding="utf-8")
    parsed = fm_parse(text)
    new_meta = dict(parsed.metadata)
    new_body = parsed.body if body is None else body
    if metadata_patch:
        new_meta.update(metadata_patch)
    new_text = fm_dumps(new_meta, new_body)

    validation = validate_metadata(new_meta, expected_schema=expected_schema)
    # Invalid drafts remain visible, with their own failing evaluation reports.
    # Do not inherit a previous version's reports or advance its approved pointer.
    with path_lock(art_store.lock_path):
        art_store._validate_stem(base.stem)  # noqa: SLF001
        version = art_store._next_version(agent_dir=base.agent_dir, stem=base.stem)  # noqa: SLF001
        new_path = base.path.parent / f"{base.stem}.{version}.md"
        atomic_write_text(new_path, new_text)
        new_ref = ArtifactRef(run_id=base.run_id, agent_dir=base.agent_dir,
                              stem=base.stem, version=version, path=new_path)
        write_reports_for_artifact(project=art_store.run.project, artifact_path=new_path,
            run_root=art_store.run.root, stem=base.stem, version=version,
            expected_schema=expected_schema or str(new_meta.get("schema", "")))
    return new_ref, validation
