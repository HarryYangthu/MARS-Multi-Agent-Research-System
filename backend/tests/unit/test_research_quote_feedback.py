"""Pure text location and optional unchanged failed-run checks; no execution substitutes."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from app.agents.idea.research_dossier import _quote_location_excerpt, _receipt_error, normalized_excerpt_text
from app.harness.schema.frontmatter_parser import parse


def test_excerpt_is_literal_bounded_context_near_the_candidate() -> None:
    page = "Opening material. " * 70 + "The authored locator target is near the end. " + "Closing material. " * 8
    quote = "The rewritten locator target near the end has unsupported extra words."
    excerpt = _quote_location_excerpt(quote, [page])
    assert 0 < len(excerpt) <= 300
    assert excerpt in normalized_excerpt_text(page)
    assert "locator target" in excerpt
    assert "unsupported extra words" not in excerpt


def test_excerpt_never_splices_disjoint_windows_or_rewrites_the_source() -> None:
    pages = ["The first authored page contains only one premise.",
             "The second authored page contains a separate conclusion."]
    quote = "The first authored page contains a separate conclusion."
    original = list(pages)
    excerpt = _quote_location_excerpt(quote, pages)
    assert any(excerpt in normalized_excerpt_text(page) for page in pages)
    assert excerpt != quote
    assert pages == original


def test_excerpt_uses_the_same_typographic_normalization_as_quote_validation() -> None:
    page = "An efﬁcient four-\n dimensional model retains its original limitations."
    excerpt = _quote_location_excerpt("An efficient four dimensional model", [page])
    assert excerpt == "An efficient four-dimensional model retains its original limitations."
    assert excerpt in normalized_excerpt_text(page)


@pytest.mark.parametrize("pages", [[], [""], ["\n  \t", ""]])
def test_empty_visible_text_never_produces_an_invented_excerpt(pages: list[str]) -> None:
    assert _quote_location_excerpt("A proposed quotation", pages) == ""


@pytest.mark.parametrize("target_at_start", [True, False])
def test_excerpt_is_bounded_at_page_edges(target_at_start: bool) -> None:
    target = "A distinctive authored boundary phrase."
    padding = "Unrelated surrounding text. " * 40
    page = target + padding if target_at_start else padding + target
    excerpt = _quote_location_excerpt(target + " altered", [page])
    assert len(excerpt) <= 300
    assert target in excerpt
    assert excerpt in normalized_excerpt_text(page)


def test_real_failed_quote_remains_invalid_and_receives_only_visible_context() -> None:
    """Opt in with an actual failed researcher checkpoint whose PDF receipts still exist.

    This reads the recorded candidate and observations unchanged. It does not
    synthesize a quote, provider response, receipt, or successful tool result.
    """
    configured = os.environ.get("MARS_TEST_QUOTE_FAILURE_CHECKPOINT")
    if not configured:
        pytest.skip("requires an actual failed researcher checkpoint and its original PDF receipts")
    checkpoint = Path(configured)
    original_bytes = checkpoint.read_bytes()
    state = json.loads(original_bytes)
    assert state["status"] != "passed", "select an actual failed researcher checkpoint"
    metadata = parse(state["candidate"]).metadata
    before = deepcopy(metadata)
    rows = {row["read_receipt"]: row for observation in state["history"]
            if observation.get("tool") == "search.fetch_sources" and observation.get("ok")
            for row in observation["output"]["sources"] if row.get("ok") and row.get("read_receipt")}
    checked = 0
    for insight in metadata["insights"]:
        row = rows.get(insight["read_receipt"])
        if row is None:
            continue
        error = _receipt_error(insight, row)
        if error is None or not error.startswith("quote is absent"):
            continue
        assert "candidate remains invalid" in error
        assert "does not establish support" in error
        excerpt = json.loads(error.split("visible_page_excerpt=", 1)[1])
        assert 0 < len(excerpt) <= 300
        actual_pages = [page["text"] for page in row["visible_pages"] if page["page"] == insight["page"]]
        assert any(excerpt in normalized_excerpt_text(page) for page in actual_pages)
        assert _receipt_error(insight, row) is not None
        checked += 1
    assert checked > 0, "selected run must retain an actual quote mismatch and readable original evidence"
    assert metadata == before
    assert checkpoint.read_bytes() == original_bytes
