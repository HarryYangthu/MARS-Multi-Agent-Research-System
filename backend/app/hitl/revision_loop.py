"""Apply human edits / comment-driven revisions to an artifact."""
from __future__ import annotations

from typing import Any

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
    text = base.path.read_text(encoding="utf-8")
    parsed = fm_parse(text)
    new_meta = dict(parsed.metadata)
    new_body = parsed.body if body is None else body
    if metadata_patch:
        new_meta.update(metadata_patch)
    new_text = fm_dumps(new_meta, new_body)

    validation = validate_metadata(new_meta, expected_schema=expected_schema)
    # write regardless so the UI can highlight issues
    new_path = base.path.parent / f"{base.stem}.{art_store._next_version(agent_dir=base.agent_dir, stem=base.stem)}.md"  # noqa: SLF001
    new_path.write_text(new_text, encoding="utf-8")
    new_ref = ArtifactRef(
        run_id=base.run_id,
        agent_dir=base.agent_dir,
        stem=base.stem,
        version=new_path.name.rsplit(".", 2)[-2],
        path=new_path,
    )
    return new_ref, validation
