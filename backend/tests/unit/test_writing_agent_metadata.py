"""Pure report previews and actual on-disk references, without fabricated debate."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from app.agents.writing.agent import _ensure_execution_refs, _turn_preview


def test_writing_frontmatter_receives_only_bounded_public_review_previews() -> None:
    authored = "---\nschema: report.v1\n---\n# Critique\n\nNeeds measured evidence. " + "Long commentary. " * 100
    preview = _turn_preview(authored)
    assert preview.startswith("# Critique") and len(preview) == 240
    assert "schema:" not in preview
    assert _turn_preview("Short authored review") == "Short authored review"


def test_execution_refs_include_only_existing_files_and_never_duplicate(tmp_path: Path) -> None:
    for name in ("execution/run_log.approved.md", "execution/metrics.json"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Human-authored reference serialization input")
    metadata: dict[str, Any] = {"chain_refs": {"runs": ["execution/metrics.json"]}}
    _ensure_execution_refs(metadata, tmp_path)
    assert metadata["chain_refs"]["runs"] == ["execution/metrics.json", "execution/run_log.approved.md"]
    _ensure_execution_refs(metadata, tmp_path)
    assert len(metadata["chain_refs"]["runs"]) == 2


def test_missing_run_root_does_not_invent_measured_evidence() -> None:
    metadata: dict[str, Any] = {}
    _ensure_execution_refs(metadata, None)
    assert metadata == {"chain_refs": {"runs": []}}
