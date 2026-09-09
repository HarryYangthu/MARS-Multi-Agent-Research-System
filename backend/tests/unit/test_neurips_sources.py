"""Pure source contracts and immutable real archives; no substituted execution."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterator

from jsonschema import Draft202012Validator
import pytest

from app.agents.idea.research_gap import material_state
from app.agents.idea.research_review_plan import matching_source_rows
from app.agents.idea.source_identity import SourceIdentityIndex, metadata_document_keys, search_metadata_rows
from app.harness.agent_loop.trace import digest
from app.harness.llm.model_registry import get_agent_config
from app.harness.tools.config import load_tool_configs
from app.harness.tools.registry import get_registry
from app.harness.tools.search.neurips import (
    _parameters, _paper_id, parse_neurips_directory, parse_neurips_landing,
    select_neurips_entries, validate_html_transfer, verified_neurips_hit,
)
from app.settings import Settings

REAL_HASHES = {"directory.html": "92a206ec3e110f72c1ac352a40291a5ca821878ddac9064551271339e1be4fd9",
               "landing.html": "9295d5476505121cfb72b0c80954520a66a7e353588577c32ec1a205993a2573"}


@pytest.fixture(scope="module")
def real_html() -> Iterator[tuple[str, str, dict[str, str]]]:
    supplied = os.environ.get("MARS_TEST_NEURIPS_HTML_ROOT")
    if not supplied:
        pytest.skip("requires complete original NeurIPS directory and landing HTML")
    root = Path(supplied)
    originals = {name: (root / name).read_bytes() for name in REAL_HASHES}
    assert {name: hashlib.sha256(raw).hexdigest() for name, raw in originals.items()} == REAL_HASHES
    directory, landing = originals["directory.html"].decode(), originals["landing.html"].decode()
    entry = select_neurips_entries(parse_neurips_directory(directory, year=2020), "Fourier Features Let Networks", 1)[0]
    yield directory, landing, entry
    assert all((root / name).read_bytes() == raw for name, raw in originals.items())


def test_real_directory_actual_pdf_button_and_unverified_citation_host(real_html: tuple[str, str, dict[str, str]]) -> None:
    directory, landing, entry = real_html
    entries = parse_neurips_directory(directory, year=2020)
    assert len(entries) == 1898
    assert select_neurips_entries(entries, "FOURIER features networks", 3) == [entry]
    assert select_neurips_entries(entries, "Fourier nonexistentword", 3) == []
    hit = parse_neurips_landing(landing, entry=entry, year=2020)
    assert hit["title"] == entry["title"] and len(hit["authors"]) == 9
    assert hit["published"] == "2020" and hit["doi"] == ""
    assert hit["pdf_url"] == "https://papers.nips.cc" + hit["paper_href"]
    assert hit["citation_pdf_url"].startswith("https://proceedings.neurips.cc/")
    assert hit["citation_pdf_alias_verified"] is False
    # Alternate citation host is never silently registered as a PDF alias.
    assert all("proceedings.neurips.cc" not in key for key in metadata_document_keys(hit))
    assert verified_neurips_hit(hit) is False  # Parsing HTML is not a tool receipt.


@pytest.mark.parametrize("change", ["empty", "truncated", "missing_title", "missing_pdf_url", "missing_author", "missing_publication_date", "missing_journal_title", "wrong_date", "wrong_venue", "duplicate_title", "duplicate_author", "duplicate_attribute", "cross_host", "http_pdf", "wrong_year", "wrong_document", "missing_button", "other_button", "ambiguous_button", "alter_page_title"])
def test_real_landing_conflicts_fail_closed(real_html: tuple[str, str, dict[str, str]], change: str) -> None:
    _, landing, entry = real_html
    if change == "empty": landing = ""
    elif change == "truncated": landing = landing[:len(landing)//2]
    elif change.startswith("missing_") and change != "missing_button":
        landing = re.sub(r'<meta name="citation_' + change.removeprefix("missing_") + r'"[^>]*>', "", landing)
    elif change == "wrong_date": landing = landing.replace('name="citation_publication_date" content="2020"', 'name="citation_publication_date" content="2021"')
    elif change == "wrong_venue": landing = landing.replace('name="citation_journal_title" content="Advances in Neural Information Processing Systems"', 'name="citation_journal_title" content="Another proceedings"')
    elif change == "duplicate_title": landing = landing.replace("</head>", '<meta name="citation_title" content="' + entry["title"] + '"></head>')
    elif change == "duplicate_author": landing = landing.replace("</head>", '<meta name="citation_author" content="Tancik, Matthew"></head>')
    elif change == "duplicate_attribute": landing = landing.replace('name="citation_title"', 'name="citation_title" content="Conflict"')
    elif change == "cross_host": landing = landing.replace("https://proceedings.neurips.cc/", "https://proceedings.neurips.cc.invalid/")
    elif change == "http_pdf": landing = landing.replace("https://proceedings.neurips.cc/", "http://proceedings.neurips.cc/")
    elif change == "wrong_year": landing = landing.replace("/paper/2020/file/", "/paper/2021/file/")
    elif change == "wrong_document": landing = landing.replace("55053683268957697aa39fba6f231c68-Paper.pdf", "0"*32 + "-Paper.pdf")
    elif change == "missing_button": landing = landing.replace(">Paper</a>", ">Other</a>")
    elif change == "other_button": landing = landing.replace("-AuthorFeedback.pdf'>AuthorFeedback</a>", "-AuthorFeedback.pdf'>Paper</a>").replace("-Paper.pdf'>Paper</a>", "-Paper.pdf'>Other</a>")
    elif change == "ambiguous_button": landing = landing.replace("</body>", '<a href="' + entry["href"] + '">Paper</a></body>')
    elif change == "alter_page_title": landing = landing.replace('<h1 class="paper-title">' + entry["title"], '<h1 class="paper-title">Changed title')
    with pytest.raises(ValueError): parse_neurips_landing(landing, entry=entry, year=2020)


@pytest.mark.parametrize("change", ["truncated", "wrong_year", "cross_host", "duplicate_entry", "missing_count", "wrong_count", "wrong_header", "missing_anchor_label"])
def test_real_directory_coverage_and_identity_fail_closed(real_html: tuple[str, str, dict[str, str]], change: str) -> None:
    directory, _, entry = real_html
    if change == "truncated": directory = directory[:-20]
    elif change == "wrong_year": directory = directory.replace(entry["href"], entry["href"].replace("2020", "2021"))
    elif change == "cross_host": directory = directory.replace(entry["href"], "https://example.invalid" + entry["href"])
    elif change == "duplicate_entry": directory = directory.replace("</body>", '<a title="paper title" href="' + entry["href"] + '">Duplicate</a></body>')
    elif change == "missing_count": directory = directory.replace('class="paper-count"', 'class="other"')
    elif change == "wrong_count": directory = directory.replace('1898 papers', '1899 papers')
    elif change == "wrong_header": directory = directory.replace('NeurIPS 2020', 'NeurIPS 2021')
    elif change == "missing_anchor_label": directory = directory.replace('title="paper title" href="' + entry["href"], 'title="other" href="' + entry["href"])
    with pytest.raises(ValueError): parse_neurips_directory(directory, year=2020)


@pytest.mark.parametrize("url", [
    "http://papers.nips.cc/paper_files/paper/2020/file/" + "a"*32 + "-Paper.pdf",
    "https://papers.nips.cc.invalid/paper_files/paper/2020/file/" + "a"*32 + "-Paper.pdf",
    "https://user@papers.nips.cc/paper_files/paper/2020/file/" + "a"*32 + "-Paper.pdf",
    "https://papers.nips.cc:9000/paper_files/paper/2020/file/" + "a"*32 + "-Paper.pdf",
    "https://papers.nips.cc/paper_files/paper/2020/file/%2e%2e/" + "a"*32 + "-Paper.pdf",
    "https://papers.nips.cc/paper_files/paper/2020/file/" + "a"*32 + "-Paper.pdf?x=1",
    "https://papers.nips.cc/paper_files/paper/2020/file/" + "a"*32 + "-Paper.pdf#x",
    "https://proceedings.neurips.cc/paper_files/paper/2020/file/" + "a"*32 + "-Paper.pdf",
    "https://papers.nips.cc/paper_files/paper/2020/file/" + "a"*32 + "-Supplemental.pdf",
    "https://papers.nips.cc/paper/2020/file/" + "a"*32 + "-Paper.pdf",
])
def test_download_identity_never_guesses_or_follows_alternate_host(url: str) -> None:
    with pytest.raises(ValueError): _paper_id(url, 2020, pdf=True)


@pytest.mark.parametrize("change", [{"query": " "}, {"query": "a"*201}, {"query": True}, {"year": 1999}, {"year": 2101}, {"year": "2020"}, {"year": True}, {"top_k": 4}, {"top_k": True}, {"venue": "NeurIPS"}, {"pdf_url": "https://example.invalid/x.pdf"}])
def test_invalid_registered_arguments_and_parser_agree(change: dict[str, Any]) -> None:
    args = {"query": "Fourier features", "year": 2020, **change}
    spec = get_registry().spec("search.neurips_search")
    assert spec is not None and list(Draft202012Validator(spec.input_schema).iter_errors(args))
    with pytest.raises(ValueError): _parameters(args)


def test_registration_is_read_only_opt_in_with_separate_metadata_budget() -> None:
    registry = get_registry(); spec = registry.spec("search.neurips_search")
    assert registry.has("search.neurips_search") and spec is not None
    assert spec.policy.allowed_agents == ("idea_research",) and spec.policy.mutation_level == "read"
    assert spec.policy.network and not spec.policy.requires_approval and not spec.bridge_only
    for name in ("idea", "idea_research"):
        assert "search.neurips_search" not in get_agent_config(name).tools
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.mars_neurips_total_timeout_seconds == 60
    assert settings.mars_neurips_directory_max_mib == 8 and settings.mars_neurips_landing_max_kib == 256
    maximum = Settings(_env_file=None, mars_neurips_total_timeout_seconds=180)  # type: ignore[call-arg]
    assert load_tool_configs()["search.neurips_search"].timeout_seconds > maximum.mars_neurips_total_timeout_seconds
    scope = registry.scope_for_read_tools("idea_research", ("search.neurips_search", "search.fetch_sources"))
    assert scope.agent == "idea_research"


def test_complete_http_transfer_is_required() -> None:
    base = dict(status_code=200, content_type="text/html; charset=utf-8", content_encoding="", content_length="20", content_range=None, received_bytes=20, byte_limit=100)
    validate_html_transfer(**base)  # type: ignore[arg-type]
    for change in ({"status_code": 206}, {"status_code": 302}, {"content_range": "bytes 0-19/80"}, {"content_length": "21"}, {"content_encoding": "gzip"}, {"received_bytes": 0}, {"byte_limit": 19}, {"content_type": "text/plain"}):
        with pytest.raises(ValueError): validate_html_transfer(**{**base, **change})  # type: ignore[arg-type]


@pytest.mark.parametrize("hit", [{}, {"search_receipt": []}, {"search_receipt": "/nonexistent/receipt.json"}])
def test_unarchived_metadata_cannot_become_a_source(hit: dict[str, Any]) -> None:
    assert verified_neurips_hit(hit) is False
    assert search_metadata_rows({"tool": "search.neurips_search", "ok": True, "output": {"hits": [hit]}}) == []


@pytest.fixture
def real_trace() -> Iterator[list[dict[str, Any]]]:
    supplied = os.environ.get("MARS_TEST_NEURIPS_EVIDENCE_TRACE")
    if not supplied:
        pytest.skip("requires real registered NeurIPS query and PDF read; no substitute")
    path = Path(supplied); original = path.read_bytes()
    events = [json.loads(line) for line in original.splitlines()]
    observations = [e["visible"] for e in events if e["kind"] == "observation"]
    for e in events:
        if "visible" in e: assert e["visible_sha256"] == digest(e["visible"])
    protected: dict[Path, bytes] = {path: original}
    def protect(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values(): protect(item)
        elif isinstance(value, list):
            for item in value: protect(item)
        elif isinstance(value, str) and value.startswith("/") and len(value) < 1000:
            p = Path(value)
            if p.is_file() and p not in protected:
                raw = p.read_bytes(); protected[p] = raw
                if p.suffix == ".json": protect(json.loads(raw))
    protect(observations)
    yield observations
    assert all(p.read_bytes() == raw for p, raw in protected.items())


def test_actual_query_and_fresh_pdf_have_verified_independent_identity(real_trace: list[dict[str, Any]]) -> None:
    search = next(o for o in real_trace if o["tool"] == "search.neurips_search")
    hits = search_metadata_rows(search)
    assert search["ok"] and len(hits) == 1 and verified_neurips_hit(hits[0])
    assert material_state([search])["counts"]["distinct_read_publications"] == 0
    with pytest.raises(ValueError, match="actual matching source reads"):
        matching_source_rows(hits[0], [search])
    assert material_state(real_trace)["counts"]["distinct_read_publications"] == 1
    rows, bindings = matching_source_rows(hits[0], real_trace)
    assert {r["tool"] for r in rows} == {"search.neurips_search", "search.fetch_sources"}
    assert bindings
    # Generic publisher declarations retain the observed landing URL as their
    # source identity; a verified download does not change that public identity.
    read = next(o for o in real_trace if o["tool"] == "search.fetch_sources")["output"]["sources"][0]
    assert SourceIdentityIndex(real_trace).matching_hits(hits[0]["url"], read_receipt=read["read_receipt"])
    assert not SourceIdentityIndex(real_trace).matching_hits(hits[0]["citation_pdf_url"])


def test_actual_receipt_observation_and_source_claim_mutations_are_rejected(real_trace: list[dict[str, Any]]) -> None:
    search = next(o for o in real_trace if o["tool"] == "search.neurips_search")
    hit = search["output"]["hits"][0]
    for changes in ({"title": "changed"}, {"year": 2021}, {"query": "other"}, {"pdf_url": hit["citation_pdf_url"]}, {"citation_pdf_alias_verified": True}, {"metadata_response_sha256": "0"*64}):
        assert verified_neurips_hit({**hit, **changes}) is False
    changed = deepcopy(search); changed["output"]["hits"][0]["title"] = "changed"
    assert search_metadata_rows(changed) == []
    changed = deepcopy(search); changed["args"]["top_k"] = 3
    assert search_metadata_rows(changed) == []
    changed_history = deepcopy(real_trace)
    for observation in changed_history:
        if observation["tool"] == "search.fetch_sources":
            for row in observation["output"].get("sources", []): row["download_url"] = hit["citation_pdf_url"]
    assert material_state(changed_history)["counts"]["distinct_read_publications"] == 0
