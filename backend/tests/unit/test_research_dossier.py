"""Pure contract tests and optional archived real-run provenance; no service doubles."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.agents.idea.research import canonical_source
from app.agents.idea.research_dossier import dossier_errors, dossier_schema, normalized_excerpt_text, write_dossier_report


def document() -> dict[str, Any]:
    # Authored contract input, never represented as a retrieved publication.
    return {"schema": "research_report.v1", "project": "public", "human_summary": "研究输入校验。",
            "gaps": [{"id": "g1", "question": "方法有什么限制？"}], "selection_principles": ["相关且可核验"],
            "sources": [{"source_id": "s1", "url": "https://arxiv.org/abs/0000.00000",
                         "title": "Authored contract input", "decision": "use",
                         "selection_reason": "验证契约", "gap_ids": ["g1"]}],
            "insights": [{"id": "i1", "source_id": "s1", "read_receipt": "/nonexistent/read.json",
                          "document_sha256": "0" * 64, "page": 1, "quote": "Authored contract input only.",
                          "paper_finding": "待验证", "transfer_idea": "待验证", "limitations": ["未检索"]}]}


def test_schema_is_valid() -> None:
    Draft202012Validator.check_schema(dossier_schema())
    assert not list(Draft202012Validator(dossier_schema()).iter_errors(document()))


def test_authored_metadata_cannot_pass_without_observations() -> None:
    errors = dossier_errors(document(), [])
    assert any("retrieved search result" in error for error in errors)
    assert any("actual tool read receipt" in error for error in errors)
    assert any("observed 0" in error for error in errors)


@pytest.mark.parametrize("field", ["selection_reason", "gap_ids"])
def test_source_contract_requires_selection_explanation(field: str) -> None:
    data = document()
    del data["sources"][0][field]
    assert any(field in error for error in dossier_errors(data, []))


@pytest.mark.parametrize("field", ["paper_finding", "transfer_idea", "limitations", "read_receipt", "page", "quote"])
def test_insight_contract_requires_traceable_transfer(field: str) -> None:
    data = document()
    del data["insights"][0][field]
    assert any(field in error for error in dossier_errors(data, []))


def test_duplicate_and_unknown_ids_rejected() -> None:
    data = document()
    data["sources"].append(dict(data["sources"][0]))
    data["sources"][0]["gap_ids"] = ["missing"]
    errors = dossier_errors(data, [])
    assert any("duplicate source" in error for error in errors)
    assert any("unknown research gap" in error for error in errors)


def test_report_does_not_claim_unverified_input_passed(tmp_path: Path) -> None:
    path = write_dossier_report(tmp_path, document(), [])
    archived = json.loads(path.with_suffix(".json").read_text())
    assert archived["provenance_valid"] is False
    assert archived["scientific_validated"] is False
    assert "Authored contract input" in path.read_text()


def test_actual_archived_receipt_and_modified_quote() -> None:
    archive = os.environ.get("MARS_TEST_RESEARCH_OBSERVATIONS")
    if not archive:
        pytest.skip("requires explicit actual-run tool_results.v1.json archive")
    observations = json.loads(Path(archive).read_text())
    row = next(row for obs in observations if obs.get("tool") == "search.fetch_sources"
               for row in obs["output"]["sources"] if row.get("ok") and row.get("visible_pages"))
    hit = next(hit for obs in observations if obs.get("tool") in {"search.arxiv_search", "search.web_search"}
               for hit in obs["output"]["hits"] if canonical_source(hit["url"]) == canonical_source(row["url"]))
    data = document()
    data["sources"][0].update(url=hit["url"], title=hit["title"])
    page = row["visible_pages"][0]
    data["insights"][0].update(read_receipt=row["read_receipt"], document_sha256=row["sha256"],
                               page=page["page"], quote=page["text"].strip()[:200])
    assert dossier_errors(data, observations) == []
    original = dict(data["insights"][0])
    for field, value, message in (("document_sha256", "0" * 64, "does not match the archived document bytes"),
                                  ("page", 100000, "is not visible"),
                                  ("read_receipt", "/nonexistent/read.json", "not in actual tool observations")):
        data["insights"][0] = {**original, field: value}
        assert any(message in error for error in dossier_errors(data, observations))
    data["insights"][0] = original
    data["insights"][0]["quote"] = "This deliberately absent quotation cannot match the archived page."
    assert any("quote is absent" in error for error in dossier_errors(data, observations))


def test_pdf_typographic_normalization_preserves_hyphens_and_words() -> None:
    assert normalized_excerpt_text("efﬁcient four-\n dimensional") == "efficient four-dimensional"
    assert normalized_excerpt_text("four-dimensional") != normalized_excerpt_text("fourdimensional")
    assert normalized_excerpt_text("x - y + 2 - 3") == "x - y + 2 - 3"
    assert normalized_excerpt_text("first\nsecond") == "first second"


def test_actual_failed_checkpoint_only_quote_provenance_repaired() -> None:
    checkpoint = os.environ.get("MARS_TEST_RESEARCH_CHECKPOINT")
    if not checkpoint:
        pytest.skip("requires explicit actual failed research checkpoint")
    from app.harness.schema.frontmatter_parser import parse
    state = json.loads(Path(checkpoint).read_text())
    metadata = parse(state["candidate"]).metadata
    errors = dossier_errors(metadata, state["history"], min_sources=2)
    assert not any("quote is absent" in error for error in errors)
    assert any("require 2 distinct read publications; observed 1" in error for error in errors)
