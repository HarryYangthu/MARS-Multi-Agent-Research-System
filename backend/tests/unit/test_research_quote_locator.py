"""Pure typography contracts and immutable real failed-run replay; no execution doubles."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata

import pytest

from app.agents.idea.research_delegate import research_excerpts
from app.agents.idea.research_dossier import (
    _receipt_error, dossier_errors, locate_quote, normalized_excerpt_text,
)
from app.harness.schema.frontmatter_parser import parse


@pytest.mark.parametrize("page,expected", [
    ("  efﬁcient four-\n dimensional model \t", "efficient four-dimensional model"),
    ("initial-\nization remains hyphenated in the excerpt", "initial-ization remains hyphenated in the excerpt"),
    ("B-\nspline and four-dimensional", "B-spline and four-dimensional"),
    ("a-\nb-\nc", "a-b-c"),
    ("a -\nb", "a - b"),
    ("x - y + 2 - 3", "x - y + 2 - 3"),
    ("first\n\nsecond", "first second"),
    ("alpha-\t beta", "alpha-beta"),
    ("１２\u00a0values", "12 values"),
    ("", ""),
])
def test_existing_normalized_page_text_is_unchanged(page: str, expected: str) -> None:
    assert normalized_excerpt_text(page) == expected


@pytest.mark.parametrize("line_break", ["\n", "\r\n", "\r", " \t\n\t "])
def test_only_verified_line_end_hyphens_can_be_omitted(line_break: str) -> None:
    page = "Prefix. The initial-" + line_break + "ization is ran-" + line_break + "domly chosen. Suffix."
    quote = "The initialization is randomly chosen."
    before = page
    match = locate_quote(quote, page)
    assert match is not None and match.normalization_mode == "pdf_line_end_hyphen"
    original = normalized_excerpt_text(page)
    assert match.start == original.index("The initial-")
    assert original[match.start:match.end] == "The initial-ization is ran-domly chosen."
    assert len(match.omitted_hyphen_offsets) == 2
    reconstructed = "".join(original[i] for i in range(match.start, match.end)
                            if i not in match.omitted_hyphen_offsets)
    assert reconstructed == quote
    assert all(original[i] == "-" for i in match.omitted_hyphen_offsets)
    assert page == before


def test_one_quote_can_retain_compound_hyphens_and_omit_other_line_end_hyphens() -> None:
    page = "The four-\ndimensional B-\nspline initial-\nization uses matrix-valued functions."
    quote = "The four-dimensional B-spline initialization uses matrix-valued functions."
    match = locate_quote(quote, page)
    assert match is not None and match.normalization_mode == "pdf_line_end_hyphen"
    text = normalized_excerpt_text(page)
    assert match.omitted_hyphen_offsets == (text.index("initial-") + len("initial"),)
    assert text[match.start:match.end] == "The four-dimensional B-spline initial-ization uses matrix-valued functions."


def test_an_exact_match_anywhere_on_the_page_has_priority() -> None:
    page = "First initialization. Later initial-\nization. Last initialization."
    match = locate_quote("initialization", page)
    assert match is not None and match.normalization_mode == "exact_normalized"
    assert match.start == len("First ") and not match.omitted_hyphen_offsets
    retained = locate_quote("four-dimensional B-spline", "four-\ndimensional B-\nspline")
    assert retained is not None and retained.normalization_mode == "exact_normalized"
    assert not retained.omitted_hyphen_offsets


def test_quote_line_breaks_do_not_authorize_removal_of_source_inline_hyphens() -> None:
    assert locate_quote("initialization", "initial-ization") is None
    assert locate_quote("initial-\nization", "initialization") is None
    assert locate_quote("initial-\nization", "initial-ization") is not None


@pytest.mark.parametrize("page,quote", [
    ("four-dimensional", "fourdimensional"),
    ("matrix-valued functions", "matrixvalued functions"),
    ("B-\nspline", "Bspline"),
    ("x-\ny", "xy"),
    ("12-\n34", "1234"),
    ("ab-\nCD", "abCD"),
    ("AB-\ncd", "ABcd"),
    ("initial-\n\nization", "initialization"),
    ("initial-\n \t\nization", "initialization"),
    ("initial-\r\n\r\nization", "initialization"),
    ("initial-\fization", "initialization"),
    ("initial-\u2028ization", "initialization"),
    ("initial-  ization", "initialization"),
    ("initial-\nPage heading\nization", "initialization"),
    ("An initial-\nization.\n\nAnother paragraph.", "An initialization. Another paragraph."),
    ("First paragraph.\r\n \r\nAn initial-\nization.", "First paragraph. An initialization."),
    ("First premise. Omitted qualifier. Last conclusion.", "First premise. Last conclusion."),
    ("A ran-\ndomly initialized value is not proven optimal.", "A randomly initialized value is proven optimal."),
    ("mathemati-\ncal form of B-spline", "mathematical form of Bspline"),
    ("The exact value is 1.5.", "The exact value is 15."),
])
def test_no_inline_hyphen_loss_blank_line_join_or_substantive_rewrite(page: str, quote: str) -> None:
    assert locate_quote(quote, page) is None


def test_quote_cannot_be_assembled_from_separate_pages_or_windows() -> None:
    pages = ["This first page ends in initial-", "ization occurs on another page."]
    quote = "This first page ends in initialization occurs on another page."
    assert all(locate_quote(quote, page) is None for page in pages)
    assert all(locate_quote("The premise supports the conclusion.", page) is None for page in
               ["The premise supports", "the conclusion."])


def test_empty_quote_is_never_valid() -> None:
    assert locate_quote("", "A visible page.") is None
    assert locate_quote("  \n", "A visible page.") is None


def test_new_match_can_start_after_a_blank_line_and_span_ordinary_single_lines() -> None:
    page = "Previous paragraph.\n\nAn initial-\nization on a single\nline boundary."
    match = locate_quote("An initialization on a single line boundary.", page)
    assert match is not None and match.normalization_mode == "pdf_line_end_hyphen"
    assert match.start == normalized_excerpt_text(page).index("An initial-")


def test_multiple_possible_starts_do_not_splice_separate_occurrences() -> None:
    page = "An initial-\nization with an unrelated ending. An initial-\nization with the required ending."
    quote = "An initialization with the required ending."
    match = locate_quote(quote, page)
    assert match is not None
    text = normalized_excerpt_text(page)
    assert match.start == text.rindex("An initial-")
    assert text[match.start:match.end] == "An initial-ization with the required ending."


def test_real_run12_last_candidate_binds_original_receipts_without_rewriting_the_archive() -> None:
    configured = os.environ.get("MARS_TEST_DEHYPHENATION_CHECKPOINT")
    if not configured:
        pytest.skip("requires the actual run12 failed child checkpoint and its original PDF receipts")
    checkpoint = Path(configured)
    before = checkpoint.read_bytes()
    state = json.loads(before)
    assert state["status"] == "validation_exhausted" and state["counts"]["reflections"] == 0
    metadata = parse(state["candidate"]).metadata
    original_metadata = deepcopy(metadata)
    history = state["history"]
    original_history = deepcopy(history)
    events_path = checkpoint.parent / "events.jsonl"
    events_before = events_path.read_bytes()
    events = [json.loads(line) for line in events_before.decode().splitlines()]
    last_validation = next(e for e in reversed(events) if e["kind"] == "validation")
    assert len(last_validation["visible"]) == 3
    assert all("quote is absent" in error for error in last_validation["visible"])
    request_path = checkpoint.parents[3] / "idea/research_delegations" / checkpoint.parent.name / "request.json"
    minimum = json.loads(request_path.read_text())["min_sources"]
    rows = {row["read_receipt"]: row for observation in history
            if observation["tool"] == "search.fetch_sources" and observation["ok"]
            for row in observation["output"]["sources"] if row.get("ok") and row.get("read_receipt")}
    originals: dict[Path, bytes] = {}
    hashes: dict[Path, str] = {}
    modes: list[str] = []
    page_texts: dict[tuple[str, int], str] = {}
    for insight in metadata["insights"]:
        row = rows[insight["read_receipt"]]
        receipt_path = Path(insight["read_receipt"])
        receipt_bytes = receipt_path.read_bytes()
        originals[receipt_path] = receipt_bytes
        receipt = json.loads(receipt_bytes)
        assert all(receipt.get(field) == row.get(field) for field in
                   ("sha256", "download_path", "visible_pages", "url", "download_url"))
        pdf_path = Path(receipt["download_path"])
        hashes[pdf_path] = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        assert hashes[pdf_path] == insight["document_sha256"] == row["sha256"]
        page = next(p["text"] for p in receipt["visible_pages"] if p["page"] == insight["page"])
        # This is the old normalization algorithm, compared on actual PDF text.
        old = " ".join(re.sub(r"(?<=[A-Za-z])-\s+(?=[A-Za-z])", "-",
                              unicodedata.normalize("NFKC", page)).split())
        assert normalized_excerpt_text(page) == old
        match = locate_quote(insight["quote"], page)
        assert match is not None
        modes.append(match.normalization_mode)
        assert (normalized_excerpt_text(insight["quote"]) in old) == (match.normalization_mode == "exact_normalized")
        assert _receipt_error(insight, row) is None
        # Negative edits are parser inputs only, never substituted for a model response.
        invalid = {**insight, "quote": insight["quote"] + " Unobserved invented conclusion."}
        assert str(_receipt_error(invalid, row)).startswith("quote is absent")
        assert "does not match" in str(_receipt_error({**insight, "document_sha256": "0" * 64}, row))
        assert "is not visible" in str(_receipt_error({**insight, "page": 100000}, row))
        page_texts[(insight["read_receipt"], insight["page"])] = old
    assert modes == ["pdf_line_end_hyphen"] * 3 + ["exact_normalized"]
    assert dossier_errors(metadata, history, min_sources=minimum) == []
    windows = research_excerpts(metadata, history, context_chars=160)
    matches = {match["insight_id"]: (window, match) for window in windows for match in window["quote_matches"]}
    assert set(matches) == {insight["id"] for insight in metadata["insights"]}
    for insight in metadata["insights"]:
        window, match = matches[insight["id"]]
        text = page_texts[(window["read_receipt"], window["page"])]
        assert window["text"] == text[window["start"]:window["end"]]
        assert window["start"] <= match["start"] < match["end"] <= window["end"]
        actual = "".join(text[i] for i in range(match["start"], match["end"])
                         if i not in match["omitted_hyphen_offsets"])
        assert actual == normalized_excerpt_text(insight["quote"])
        assert match["normalization_mode"] in modes
    assert metadata == original_metadata and history == original_history
    assert checkpoint.read_bytes() == before and events_path.read_bytes() == events_before
    assert all(path.read_bytes() == data for path, data in originals.items())
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == sha for path, sha in hashes.items())
    # The original run remains failed and unreviewed; passing this contract is not scientific acceptance.
    assert json.loads(checkpoint.read_text())["status"] == "validation_exhausted"
