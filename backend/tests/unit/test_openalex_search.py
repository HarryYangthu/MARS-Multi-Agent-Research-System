"""Authored parser input and real configured refusal; no network/provider doubles."""
from __future__ import annotations

import pytest

from app.harness.tools.registry import ToolContext
from app.harness.tools.search.openalex import openalex_search_tool, parse_openalex
from app.settings import reset_settings_cache


def test_parser_preserves_original_location_and_provider_identity() -> None:
    payload = {"results": [{"id": "https://openalex.org/W0000", "title": "Authored parser input",
               "doi": "https://doi.org/10.example/input", "abstract_inverted_index": {"Test": [0], "input": [1]},
               "locations": [{"pdf_url": "https://arxiv.org/pdf/0000.00000", "landing_page_url": "http://arxiv.org/abs/0000.00000"}]}]}
    hits = parse_openalex(payload, allowed_domains=("arxiv.org",), top_k=1)
    assert hits[0]["source"] == "openalex"
    assert hits[0]["summary"] == "Test input"
    assert hits[0]["pdf_url"] == "https://arxiv.org/pdf/0000.00000"
    assert hits[0]["metadata_url"] == "https://openalex.org/W0000"
    assert parse_openalex(payload, allowed_domains=("example.org",), top_k=1) == []


@pytest.mark.asyncio
async def test_real_network_setting_refuses_disabled_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARS_ENABLE_NETWORK_TOOLS", "false")
    reset_settings_cache()
    try:
        result = await openalex_search_tool({"query": "test"}, ToolContext("test", "public", "idea"))
        assert not result.ok
        assert result.error == "network tools are disabled"
    finally:
        reset_settings_cache()
