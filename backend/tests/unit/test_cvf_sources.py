"""Pure contracts and immutable real HTML replay; no network/provider substitutes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

import pytest

from app.harness.tools.search.cvf import (
    _parameters,
    _paper_url,
    parse_cvf_directory,
    parse_cvf_landing,
    select_cvf_entries,
    validate_html_transfer,
    verified_cvf_hit,
)


REAL_HASHES = {
    "CVPR2022-all.html": "7c6a81f30b3317d1837142298c512807e62981d7184057df50acc5d6c66b131c",
    "landing.html": "f9d26d48c9bb4c28713c23200e61c23037473ca3b20c1ab7328eabe8f66db09e",
}


@pytest.fixture(scope="module")
def real_html() -> tuple[str, str, dict[str, str]]:
    value = os.environ.get("MARS_TEST_CVF_HTML_ROOT")
    if not value:
        pytest.skip("set MARS_TEST_CVF_HTML_ROOT to the immutable real CVF HTML archive")
    root = Path(value)
    data = {name: (root / name).read_bytes() for name in REAL_HASHES}
    assert {name: hashlib.sha256(raw).hexdigest() for name, raw in data.items()} == REAL_HASHES
    directory, landing = data["CVPR2022-all.html"].decode(), data["landing.html"].decode()
    entry = select_cvf_entries(parse_cvf_directory(directory, venue="CVPR", year=2022), "AdaInt", 1)[0]
    return directory, landing, entry


def test_real_directory_and_citation_metadata(real_html: tuple[str, str, dict[str, str]]) -> None:
    directory, landing, entry = real_html
    entries = parse_cvf_directory(directory, venue="CVPR", year=2022)
    assert len(entries) == 2074
    assert select_cvf_entries(entries, "ADaInt learning intervals", 3) == [entry]
    assert select_cvf_entries(entries, "AdaInt nonexistentword", 3) == []
    assert select_cvf_entries(entries, "AdaInt pdf", 3) == []
    metadata = parse_cvf_landing(landing, entry=entry, venue="CVPR", year=2022)
    assert metadata["title"] == entry["title"]
    assert metadata["published"] == "2022" and len(metadata["authors"]) == 5
    assert metadata["doi"] == ""
    assert metadata["pdf_url"] in landing
    assert metadata["url"] == entry["url"]


@pytest.mark.parametrize("change", ["empty", "truncated", "missing_title", "missing_pdf", "missing_authors", "missing_date", "wrong_date", "invalid_date", "conflict_title", "conflict_pdf", "duplicate_attribute", "cross_host", "http_pdf", "wrong_year"])
def test_real_landing_fail_closed(real_html: tuple[str, str, dict[str, str]], change: str) -> None:
    _, landing, entry = real_html
    if change == "empty":
        landing = ""
    elif change == "truncated":
        landing = landing[:len(landing) // 2]
    elif change.startswith("missing_"):
        field = {"missing_title": "title", "missing_pdf": "pdf_url", "missing_authors": "author", "missing_date": "publication_date"}[change]
        landing = re.sub(r'<meta name="citation_' + field + r'"[^>]*>', "", landing)
    elif change in {"wrong_date", "invalid_date"}:
        landing = landing.replace('name="citation_publication_date" content="2022"', 'name="citation_publication_date" content="' + ("2023" if change == "wrong_date" else "2022/99/99") + '"')
    elif change == "conflict_title":
        landing = landing.replace("</head>", '<meta name="citation_title" content="Different title"></head>')
    elif change == "conflict_pdf":
        landing = landing.replace("</head>", '<meta name="citation_pdf_url" content="https://openaccess.thecvf.com/content/CVPR2022/papers/other.pdf"></head>')
    elif change == "duplicate_attribute":
        landing = landing.replace('name="citation_title"', 'name="citation_title" content="conflict"')
    elif change == "cross_host":
        landing = landing.replace("https://openaccess.thecvf.com/content/CVPR2022/papers/", "https://example.org/content/CVPR2022/papers/")
    elif change == "http_pdf":
        landing = landing.replace("https://openaccess.thecvf.com/content/CVPR2022/papers/", "http://openaccess.thecvf.com/content/CVPR2022/papers/")
    elif change == "wrong_year":
        landing = landing.replace("/content/CVPR2022/papers/", "/content/CVPR2023/papers/")
    with pytest.raises(ValueError):
        parse_cvf_landing(landing, entry=entry, venue="CVPR", year=2022)


def test_real_directory_title_must_match_landing(real_html: tuple[str, str, dict[str, str]]) -> None:
    _, landing, entry = real_html
    with pytest.raises(ValueError, match="disagree"):
        parse_cvf_landing(landing, entry={**entry, "title": "Different title"}, venue="CVPR", year=2022)


@pytest.mark.parametrize("change", ["truncated", "cross_host", "conflicting_title", "wrong_year"])
def test_real_directory_fail_closed(real_html: tuple[str, str, dict[str, str]], change: str) -> None:
    directory, _, entry = real_html
    if change == "truncated":
        directory = directory[:-20]
    elif change == "cross_host":
        directory = directory.replace(entry["href"], "https://example.org" + entry["href"])
    elif change == "conflicting_title":
        directory = directory.replace("</html>", f'<dt class="ptitle"><a href="{entry["href"]}">Different title</a></dt></html>')
    else:
        directory = directory.replace(entry["href"], entry["href"].replace("CVPR2022", "CVPR2023"))
    with pytest.raises(ValueError):
        parse_cvf_directory(directory, venue="CVPR", year=2022)


@pytest.mark.parametrize("url", [
    "http://openaccess.thecvf.com/content/CVPR2022/papers/x.pdf",
    "https://openaccess.thecvf.com.evil.org/content/CVPR2022/papers/x.pdf",
    "https://user@openaccess.thecvf.com/content/CVPR2022/papers/x.pdf",
    "https://openaccess.thecvf.com:8000/content/CVPR2022/papers/x.pdf",
    "https://openaccess.thecvf.com/content/CVPR2022/papers/%2e%2e/x.pdf",
    "https://openaccess.thecvf.com/content/CVPR2022/papers/x.pdf#fragment",
    "https://openaccess.thecvf.com/content/CVPR2022/papers/x.pdf?other=1",
    "https://openaccess.thecvf.com/content/ICCV2022/papers/x.pdf",
])
def test_official_pdf_url_boundary(url: str) -> None:
    with pytest.raises(ValueError):
        _paper_url(url, "CVPR", 2022, pdf=True)


def test_legacy_layout_reports_unsupported_without_guessing_replacement() -> None:
    # A handwritten unsupported URL is a pure negative contract input.
    with pytest.raises(ValueError, match="unsupported legacy CVF URL layout"):
        _paper_url("https://openaccess.thecvf.com/content_CVPR_2019/papers/example.pdf", "CVPR", 2019, pdf=True)


@pytest.mark.parametrize("updates", [
    {"query": " "}, {"query": "!!!"}, {"query": " " * 200 + "a"}, {"query": 1},
    {"venue": []}, {"venue": "cvpr"}, {"year": True}, {"year": 2022.0}, {"year": 1999},
    {"top_k": True}, {"top_k": 4}, {"top_k": 0}, {"arbitrary": "path"},
])
def test_argument_contract(updates: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _parameters({"query": "AdaInt", "venue": "CVPR", "year": 2022, **updates})


def test_transport_contract() -> None:
    base: dict[str, Any] = dict(status_code=200, content_type="text/html; charset=UTF-8", content_encoding="identity", content_length="10", content_range=None, received_bytes=10, byte_limit=20)
    validate_html_transfer(**base)
    validate_html_transfer(**{**base, "content_length": None})
    for updates in ({"status_code": 206}, {"status_code": 302}, {"content_range": "bytes 0-9/20"}, {"content_length": "11"}, {"content_length": "invalid"}, {"received_bytes": 0}, {"byte_limit": 9}, {"content_encoding": "gzip"}, {"content_type": "text/html-other"}, {"content_type": "application/pdf"}):
        with pytest.raises(ValueError):
            validate_html_transfer(**{**base, **updates})


@pytest.mark.parametrize("hit", [{}, {"search_receipt": []}, {"search_receipt": "/nonexistent/receipt.json"}, {"search_receipt": None}])
def test_malformed_hit_is_false(hit: dict[str, Any]) -> None:
    assert verified_cvf_hit(hit) is False


def test_actual_registered_query_receipt_and_mutations(tmp_path: Path) -> None:
    value = os.environ.get("MARS_TEST_CVF_QUERY_RESULT")
    if not value:
        pytest.skip("set MARS_TEST_CVF_QUERY_RESULT to the single real registered query result")
    result_path = Path(value)
    original_result = result_path.read_bytes()
    result = json.loads(original_result)
    assert result["ok"] is True
    hit = result["output"]["hits"][0]
    paths = [Path(hit[key]) for key in ("search_receipt", "directory_response_ref", "metadata_response_ref")]
    originals = {path: path.read_bytes() for path in paths}
    assert verified_cvf_hit(hit) is True
    for updates in ({"title": "Changed"}, {"query": "Other"}, {"venue": "ICCV"}, {"year": 2023}, {"pdf_url": "https://example.org/paper.pdf"}, {"metadata_response_sha256": "0" * 64}, {"search_receipt_sha256": "0" * 64}):
        assert verified_cvf_hit({**hit, **updates}) is False
    # A corrupted copy is only a negative input; never synthesize a successful receipt.
    copied = tmp_path / "receipt.json"
    copied.write_bytes(originals[paths[0]] + b" ")
    assert verified_cvf_hit({**hit, "search_receipt": str(copied)}) is False
    assert result_path.read_bytes() == original_result
    assert all(path.read_bytes() == original for path, original in originals.items())
