"""Search parsing, real filesystem archives and real network gates; no transport doubles."""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.harness.tools import search as search_tools
from app.harness.tools.search.source_fetch import allowed_url, extract_pdf
from app.harness.tools.registry import ToolContext, reset_for_tests
from app.harness.llm.model_registry import reset_cache_for_tests as reset_model_cache
from app.harness.kb.stores import reset_for_tests as reset_stores
from app.settings import reset_settings_cache


@pytest.fixture(autouse=True)
def reset_search_settings() -> Iterator[None]:
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.mark.asyncio
async def test_arxiv_enabled_but_network_switch_blocks_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARS_ENABLE_NETWORK_TOOLS", "false")
    reset_model_cache()
    reg = reset_for_tests()
    spec = reg.spec("search.arxiv_search")
    result = await reg.dispatch("search.arxiv_search", {"q": "massive MIMO", "top_k": 1},
                                ToolContext("r1", "pimc", "idea", extra={"run_root": str(tmp_path)}))
    assert spec is not None and spec.policy.network is True
    assert result.ok is False
    assert "network tools are disabled" in str(result.error)


def test_arxiv_parser_and_date_filter_on_authored_xml() -> None:
    # Parser input, not an HTTP response or retrieved publication.
    xml = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <id>http://arxiv.org/abs/2501.00001v1</id><title> Authored parser input </title>
      <summary>  Whitespace normalization contract. </summary>
      <published>2025-01-01T00:00:00Z</published><author><name>Parser author</name></author>
    </entry></feed>"""
    hits = search_tools._parse_arxiv(xml, date_from="")
    assert hits[0]["id"] == "2501.00001v1"
    assert hits[0]["title"] == "Authored parser input"
    assert hits[0]["authors"] == ["Parser author"]
    assert hits[0]["pdf_url"] == "https://arxiv.org/pdf/2501.00001v1.pdf"
    assert search_tools._parse_arxiv(xml, date_from="2026-01-01") == []


def test_pdf_signature_and_blank_pages_cannot_claim_a_read() -> None:
    with pytest.raises(ValueError, match="signature"):
        extract_pdf(b"not PDF content", start_page=1, max_pages=1, max_chars=1000)
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    stream = BytesIO()
    writer.write(stream)
    with pytest.raises(ValueError, match="no extractable text"):
        extract_pdf(stream.getvalue(), start_page=1, max_pages=1, max_chars=1000)
    with pytest.raises(ValueError, match="start_page"):
        extract_pdf(stream.getvalue(), start_page=2, max_pages=1, max_chars=1000)


@pytest.mark.asyncio
async def test_unallowlisted_download_keeps_an_honest_failure_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARS_ENABLE_NETWORK_TOOLS", "true")
    monkeypatch.setenv("MARS_WEB_SEARCH_ALLOWLIST", "arxiv.org")
    result = await search_tools.fetch_sources_tool(
        {"sources": [{"title": "Unauthorized input", "url": "https://not-allowed.example/paper"}]},
        ToolContext("blocked-download", "pimc", "idea", extra={"run_root": str(tmp_path)}))
    assert result.ok is False
    row = result.output["sources"][0]
    assert row["ok"] is False
    assert row["network_download_attempted"] is False
    assert "not allowlisted" in row["error"]
    saved = json.loads(Path(result.output["index_path"]).read_text())
    assert saved[-1] == row
    assert not list(tmp_path.rglob("*.pdf"))


@pytest.mark.parametrize("url", [
    "http://arxiv.org/pdf/a", "https://user:password@arxiv.org/pdf/a",
    "https://arxiv.org:8443/pdf/a", "https://arxiv.org.not-allowed.example/pdf/a",
])
def test_source_url_rejects_scheme_credentials_port_and_host_spoofing(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARS_WEB_SEARCH_ALLOWLIST", "arxiv.org")
    with pytest.raises(ValueError):
        allowed_url(url)


def test_domain_filter_is_not_a_substring_match() -> None:
    # Untrusted rows supplied to the pure filter, not fake service replies.
    rows = [{"url": "https://arxiv.org/abs/a"}, {"url": "https://export.arxiv.org/api/query"},
            {"url": "https://arxiv.org.evil.example/a"}, {"url": "https://evil.example/arxiv.org"}]
    assert search_tools._filter_hits_by_domain(rows, ("arxiv.org",)) == rows[:2]


@pytest.mark.asyncio
async def test_web_search_requires_provider_when_network_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARS_ENABLE_NETWORK_TOOLS", "true")
    monkeypatch.setenv("MARS_WEB_SEARCH_ALLOWLIST", "arxiv.org")
    monkeypatch.setenv("MARS_WEB_SEARCH_PROVIDER", "")
    result = await search_tools.web_search_tool({"q": "massive mimo", "domains": ["arxiv.org"]},
                                                ToolContext("r1", "pimc", "idea"))
    assert result.ok is False
    assert "provider is not configured" in str(result.error)


@pytest.mark.asyncio
async def test_web_search_rejects_domains_outside_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARS_ENABLE_NETWORK_TOOLS", "true")
    monkeypatch.setenv("MARS_WEB_SEARCH_ALLOWLIST", "arxiv.org")
    result = await search_tools.web_search_tool({"q": "massive mimo", "domains": ["example.com"]},
                                                ToolContext("r1", "pimc", "idea"))
    assert result.ok is False
    assert "not allowlisted" in str(result.error)


@pytest.mark.asyncio
async def test_real_arxiv_cache_and_pdf_archive_when_network_opted_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("MARS_RUN_EXTERNAL_TOOL_SMOKE") != "true":
        pytest.skip("actual external arXiv/PDF integration requires network opt-in")
    monkeypatch.setenv("MARS_ENABLE_NETWORK_TOOLS", "true")
    monkeypatch.setenv("MARS_WEB_SEARCH_ALLOWLIST", "arxiv.org,export.arxiv.org")
    reset_stores(tmp_path / "knowledge")
    ctx = ToolContext("actual-download", "pimc", "idea", extra={"run_root": str(tmp_path)})
    try:
        first = await search_tools.arxiv_search_tool({"query": "id:1907.02350", "top_k": 1}, ctx)
        assert first.ok, first.error
        assert first.output["hits"]
        second = await search_tools.arxiv_search_tool({"query": "id:1907.02350", "top_k": 1}, ctx)
        assert second.ok and second.output["cached"] is True
        assert second.output["hits"] == first.output["hits"]
        source = first.output["hits"][0]
        result = await search_tools.fetch_sources_tool({"sources": [source], "max_pages": 2}, ctx)
        assert result.ok, result.output
        row = result.output["sources"][0]
        assert Path(row["download_path"]).read_bytes().startswith(b"%PDF-")
        assert hashlib.sha256(Path(row["download_path"]).read_bytes()).hexdigest() == row["sha256"]
        reused = await search_tools.fetch_sources_tool({"sources": [source], "start_page": 2, "max_pages": 1}, ctx)
        assert reused.ok, reused.output
        assert reused.output["sources"][0]["reused"] is True
        assert reused.output["sources"][0]["network_download_performed"] is False
        assert reused.output["sources"][0]["visible_pages"][0]["page"] == 2
        # A disallowed second source must not erase an earlier successful download.
        partial = await search_tools.fetch_sources_tool(
            {"sources": [source, {"title": "Unallowlisted input", "url": "https://not-allowed.example/p"}],
             "max_sources": 2}, ctx)
        assert partial.ok
        assert partial.output["sources"][0]["ok"] is True
        assert partial.output["sources"][1]["ok"] is False
    finally:
        reset_stores()


@pytest.mark.asyncio
async def test_web_search_provider_external_smoke_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("MARS_RUN_EXTERNAL_TOOL_SMOKE") != "true":
        pytest.skip("actual external web search requires network opt-in")
    provider = os.environ.get("MARS_WEB_SEARCH_PROVIDER", "")
    key_env = {"brave": "BRAVE_SEARCH_API_KEY", "tavily": "TAVILY_API_KEY",
               "serper": "SERPER_API_KEY", "zhipu": "ZHIPU_API_KEY"}.get(provider)
    if not key_env or not os.environ.get(key_env):
        pytest.skip("a real configured web search provider/key is required")
    monkeypatch.setenv("MARS_ENABLE_NETWORK_TOOLS", "true")
    monkeypatch.setenv("MARS_WEB_SEARCH_ALLOWLIST", "arxiv.org")
    result = await search_tools.web_search_tool({"q": "massive MIMO", "domains": ["arxiv.org"], "top_k": 1},
                                                ToolContext("external-search", "pimc", "idea"))
    assert result.ok, result.error
    assert result.output["hits"]
