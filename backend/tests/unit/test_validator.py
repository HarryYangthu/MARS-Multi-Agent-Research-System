from __future__ import annotations

from app.harness.schema.validator import (
    SUPPORTED_SCHEMAS,
    get_schema,
    validate_document,
)


def test_all_schema_files_load() -> None:
    for sid in SUPPORTED_SCHEMAS:
        schema = get_schema(sid)
        assert schema["$id"].endswith(f"{sid}.json")
        assert schema["properties"]["schema"]["const"] == sid


def test_validate_document_unknown_schema_returns_error() -> None:
    text = "---\nschema: bogus.v9\nproject: x\nagent: idea\n---\n"
    result = validate_document(text)
    assert not result.valid
    assert any("bogus.v9" in e.message for e in result.errors)


def test_validate_document_no_frontmatter_fails() -> None:
    result = validate_document("just markdown")
    assert not result.valid
    assert any("schema" in e.path for e in result.errors)


def test_expected_schema_mismatch_flagged() -> None:
    text = (
        "---\nschema: proposal.v1\nproject: x\nagent: idea\n"
        "research_question: enough chars here\n"
        "hypothesis: enough chars here\n"
        "novelty: enough chars here\n---\n"
    )
    result = validate_document(text, expected_schema="report.v1")
    assert not result.valid
    assert any("expected schema 'report.v1'" in e.message for e in result.errors)
