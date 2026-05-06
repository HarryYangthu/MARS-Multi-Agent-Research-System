"""The 5 template artifact files (templates/artifacts/*.md) must validate."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.schema.validator import SUPPORTED_SCHEMAS, validate_document

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = REPO_ROOT / "templates" / "artifacts"


@pytest.mark.parametrize("schema_id", SUPPORTED_SCHEMAS)
def test_template_validates(schema_id: str) -> None:
    path = TEMPLATES / f"{schema_id}.md"
    assert path.exists(), f"template missing: {path}"
    text = path.read_text(encoding="utf-8")
    result = validate_document(text, expected_schema=schema_id)
    assert result.valid, f"template {path} failed: {result.errors}"
