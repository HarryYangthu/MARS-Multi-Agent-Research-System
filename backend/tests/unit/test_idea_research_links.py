"""Pure link consistency checks; these inputs do not simulate agent execution."""
from __future__ import annotations

from typing import Any

import pytest

from app.agents.idea.research_links import research_link_errors


def documents() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metadata: dict[str, Any] = {
        "method_spec": {"candidate": {"definition": "Human-authored pointer target"}},
        "related_literature": [{"url": "https://arxiv.org/abs/1907.02350"}],
        "research_links": [{"delegation_id": "reading_a", "insight_id": "finding_a",
            "method_spec_ref": "/method_spec/candidate",
            "adaptation_reason": "Human-authored syntax-check input; no scientific claim."}],
    }
    reports: list[dict[str, Any]] = [{"delegation_id": "reading_a", "report": {
        "sources": [{"source_id": "paper_a", "decision": "use", "url": "https://arxiv.org/pdf/1907.02350v4"}],
        "insights": [{"id": "finding_a", "source_id": "paper_a"}],
    }}]
    return metadata, reports


def test_consistent_pointers_only_do_not_validate_research_provenance() -> None:
    metadata, reports = documents()
    assert research_link_errors(metadata, reports) == []
    assert research_link_errors(metadata, [])


@pytest.mark.parametrize("field,value", [
    ("delegation_id", "unknown"), ("insight_id", "unknown"),
    ("method_spec_ref", "/method_spec/missing"), ("method_spec_ref", "/related_literature/0"),
    ("adaptation_reason", ""),
])
def test_invalid_research_link(field: str, value: str) -> None:
    metadata, reports = documents()
    metadata["research_links"][0][field] = value
    assert research_link_errors(metadata, reports)


def test_reference_cannot_cross_delegation_boundary() -> None:
    metadata, reports = documents()
    reports.append({"delegation_id": "reading_b", "report": {"sources": [], "insights": []}})
    metadata["research_links"][0]["delegation_id"] = "reading_b"
    assert research_link_errors(metadata, reports)


def test_discarded_source_and_missing_citation_are_rejected() -> None:
    metadata, reports = documents()
    reports[0]["report"]["sources"][0]["decision"] = "reject"
    metadata["related_literature"] = []
    errors = research_link_errors(metadata, reports)
    assert any("not selected" in error for error in errors)
    assert any("cite" in error for error in errors)


def test_duplicate_links_are_not_extra_evidence() -> None:
    metadata, reports = documents()
    metadata["research_links"] *= 2
    assert any("duplicate" in error for error in research_link_errors(metadata, reports))
