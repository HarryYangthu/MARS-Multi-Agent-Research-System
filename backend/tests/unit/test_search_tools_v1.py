from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.harness.tools import search as search_tools
from app.harness.llm.model_registry import reset_cache_for_tests as reset_model_cache
from app.harness.tools.registry import ToolContext, reset_for_tests


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    import app.settings as settings_mod

    settings_mod._settings = None
    yield
    settings_mod._settings = None


@pytest.mark.asyncio
async def test_arxiv_enabled_but_network_switch_blocks_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_ENABLE_NETWORK_TOOLS", "false")
    reset_model_cache()
    reg = reset_for_tests()
    spec = reg.spec("search.arxiv_search")

    result = await reg.dispatch(
        "search.arxiv_search",
        {"q": "massive MIMO", "top_k": 1},
        ToolContext(
            run_id="r1",
            project="moe-pimc",
            agent="idea",
            extra={"run_root": str(tmp_path / "runs" / "r1")},
        ),
    )

    assert spec is not None
    assert spec.policy.network is True
    assert result.ok is False
    assert "network tools are disabled" in str(result.error)


@pytest.mark.asyncio
async def test_arxiv_search_parses_httpx_response_and_uses_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_ENABLE_NETWORK_TOOLS", "true")
    import app.settings as settings_mod

    settings_mod._settings = None
    cache_dir = tmp_path / "arxiv_cache"
    calls: list[str] = []
    writes: list[dict[str, Any]] = []
    xml = """\
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2501.00001v1</id>
    <title> Massive MIMO PIM </title>
    <summary> Summary text </summary>
    <published>2025-01-01T00:00:00Z</published>
    <author><name>Ada</name></author>
  </entry>
</feed>
"""

    class FakeResponse:
        text = xml

        def raise_for_status(self) -> None:
            return None

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

        async def get(self, url: str) -> FakeResponse:
            calls.append(url)
            return FakeResponse()

    async def no_rate_limit() -> None:
        return None

    def fake_write_to_zone(**kwargs: Any) -> str:
        writes.append(kwargs)
        return "memory-id"

    monkeypatch.setattr("app.harness.tools.search.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(search_tools, "_respect_arxiv_rate_limit", no_rate_limit)
    monkeypatch.setattr(search_tools, "_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(search_tools, "write_to_zone", fake_write_to_zone)

    first = await search_tools.arxiv_search_tool(
        {"q": "massive MIMO", "top_k": 1},
        ToolContext(run_id="r1", project="moe-pimc", agent="idea"),
    )

    assert first.ok is True
    assert first.output["cached"] is False
    assert first.output["hits"][0]["id"] == "2501.00001v1"
    assert first.output["hits"][0]["title"] == "Massive MIMO PIM"
    assert first.output["hits"][0]["authors"] == ["Ada"]
    assert writes[0]["metadata"]["source"] == "arxiv"
    assert len(calls) == 1
    assert len(list(cache_dir.glob("*.json"))) == 1

    second = await search_tools.arxiv_search_tool(
        {"q": "massive MIMO", "top_k": 1},
        ToolContext(run_id="r1", project="moe-pimc", agent="idea"),
    )

    assert second.ok is True
    assert second.output["cached"] is True
    assert second.output["hits"][0]["id"] == "2501.00001v1"
    assert len(calls) == 1
    assert len(writes) == 1


@pytest.mark.asyncio
async def test_web_search_requires_provider_when_network_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_ENABLE_NETWORK_TOOLS", "true")
    monkeypatch.setenv("MARS_WEB_SEARCH_ALLOWLIST", "arxiv.org")
    monkeypatch.setenv("MARS_WEB_SEARCH_PROVIDER", "")
    import app.settings as settings_mod

    settings_mod._settings = None

    result = await search_tools.web_search_tool(
        {"q": "massive mimo", "domains": ["arxiv.org"]},
        ToolContext(run_id="r1", project="moe-pimc", agent="idea"),
    )

    assert result.ok is False
    assert "provider is not configured" in str(result.error)


@pytest.mark.asyncio
async def test_web_search_filters_provider_results_to_allowlisted_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_ENABLE_NETWORK_TOOLS", "true")
    monkeypatch.setenv("MARS_WEB_SEARCH_ALLOWLIST", "arxiv.org,example.edu")
    monkeypatch.setenv("MARS_WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    import app.settings as settings_mod

    settings_mod._settings = None

    async def fake_provider(**_kwargs: Any) -> list[dict[str, str]]:
        return [
            {
                "title": "allowed",
                "url": "https://arxiv.org/abs/1234.5678",
                "snippet": "paper",
                "source": "fake",
            },
            {
                "title": "blocked",
                "url": "https://not-allowed.example/search",
                "snippet": "nope",
                "source": "fake",
            },
        ]

    monkeypatch.setattr(search_tools, "_call_web_search_provider", fake_provider)

    result = await search_tools.web_search_tool(
        {"q": "massive mimo", "domains": ["arxiv.org"], "top_k": 5},
        ToolContext(run_id="r1", project="moe-pimc", agent="idea"),
    )

    assert result.ok is True
    assert result.output["hits"] == [
        {
            "title": "allowed",
            "url": "https://arxiv.org/abs/1234.5678",
            "snippet": "paper",
            "source": "fake",
        }
    ]


@pytest.mark.asyncio
async def test_web_search_rejects_domains_outside_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_ENABLE_NETWORK_TOOLS", "true")
    monkeypatch.setenv("MARS_WEB_SEARCH_ALLOWLIST", "arxiv.org")
    monkeypatch.setenv("MARS_WEB_SEARCH_PROVIDER", "tavily")
    import app.settings as settings_mod

    settings_mod._settings = None

    result = await search_tools.web_search_tool(
        {"q": "massive mimo", "domains": ["example.com"]},
        ToolContext(run_id="r1", project="moe-pimc", agent="idea"),
    )

    assert result.ok is False
    assert "not allowlisted" in str(result.error)


@pytest.mark.asyncio
async def test_web_search_provider_external_smoke_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.environ.get("MARS_RUN_EXTERNAL_TOOL_SMOKE") != "true":
        pytest.skip("external web search smoke is opt-in")
    provider = os.environ.get("MARS_WEB_SEARCH_PROVIDER", "")
    if provider not in {"brave", "tavily", "serper"}:
        pytest.skip("set MARS_WEB_SEARCH_PROVIDER=brave|tavily|serper")
    key_env = {
        "brave": "BRAVE_SEARCH_API_KEY",
        "tavily": "TAVILY_API_KEY",
        "serper": "SERPER_API_KEY",
    }[provider]
    if not os.environ.get(key_env):
        pytest.skip(f"{key_env} is required for external web search smoke")

    monkeypatch.setenv("MARS_ENABLE_NETWORK_TOOLS", "true")
    monkeypatch.setenv("MARS_WEB_SEARCH_ALLOWLIST", "arxiv.org")
    import app.settings as settings_mod

    settings_mod._settings = None
    result = await search_tools.web_search_tool(
        {"q": "massive MIMO", "domains": ["arxiv.org"], "top_k": 1},
        ToolContext(run_id="r1", project="moe-pimc", agent="idea"),
    )

    assert result.ok is True
    assert result.output["hits"]
