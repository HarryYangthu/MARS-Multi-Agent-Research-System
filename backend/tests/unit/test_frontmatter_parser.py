from __future__ import annotations

import pytest

from app.harness.schema.frontmatter_parser import (
    FrontmatterError,
    close_unclosed_frontmatter,
    dumps,
    parse,
)


def test_parse_basic() -> None:
    text = "---\nschema: proposal.v1\nproject: x\n---\n# body\n"
    doc = parse(text)
    assert doc.metadata["schema"] == "proposal.v1"
    assert doc.metadata["project"] == "x"
    assert "# body" in doc.body


def test_parse_no_frontmatter_returns_empty_metadata() -> None:
    doc = parse("# just body\n")
    assert doc.metadata == {}
    assert "# just body" in doc.body


def test_dumps_round_trip() -> None:
    md = {"schema": "proposal.v1", "project": "x", "agent": "idea"}
    text = dumps(md, "body\n")
    doc = parse(text)
    assert doc.metadata["schema"] == "proposal.v1"
    assert "body" in doc.body


def test_parse_invalid_yaml_raises() -> None:
    text = "---\n: : :\n---\n"
    with pytest.raises(FrontmatterError):
        parse(text)


def test_close_unclosed_frontmatter_from_llm_output() -> None:
    text = "---\nschema: proposal.v1\nproject: pimc\n# Body\n"
    repaired = close_unclosed_frontmatter(text)
    doc = parse(repaired)

    assert doc.metadata["schema"] == "proposal.v1"
    assert doc.metadata["project"] == "pimc"
    assert doc.body.startswith("# Body")
