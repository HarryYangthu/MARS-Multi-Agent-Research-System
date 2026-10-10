"""Real settings inputs; no model or network response substitutes."""
from pathlib import Path

import pytest

from app.harness.tools.search.readiness import LiteratureAccessError, require_literature_access
from app.settings import Settings


def test_missing_runtime_config_uses_working_literature_defaults(tmp_path: Path) -> None:
    require_literature_access(Settings(_env_file=tmp_path / "missing.env"))  # type: ignore[call-arg]


@pytest.mark.parametrize(("enabled", "domains", "code"), [
    (False, "arxiv.org", "literature_network_disabled"),
    (True, " , ", "literature_source_domains_empty"),
])
def test_unavailable_literature_stops_before_model_work(enabled: bool, domains: str, code: str) -> None:
    configured = Settings(_env_file=None, mars_enable_network_tools=enabled, mars_web_search_allowlist=domains)  # type: ignore[call-arg]
    with pytest.raises(LiteratureAccessError, match="尚未调用研究模型") as failure:
        require_literature_access(configured)
    assert failure.value.reason["code"] == code


@pytest.mark.asyncio
@pytest.mark.parametrize(("enabled", "domains", "code"), [
    ("false", "arxiv.org", "literature_network_disabled"),
    ("true", "", "literature_source_domains_empty"),
])
async def test_actual_research_context_rejects_missing_access_before_trace_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, enabled: str, domains: str, code: str,
) -> None:
    from app.agents.base import RunRequest
    from app.agents.idea.focused_agent import FocusedIdeaAgent
    from app.settings import reset_settings_cache

    monkeypatch.setenv("MARS_ENABLE_NETWORK_TOOLS", enabled)
    monkeypatch.setenv("MARS_WEB_SEARCH_ALLOWLIST", domains)
    reset_settings_cache()
    try:
        agent = FocusedIdeaAgent()
        request = RunRequest(project="pimc", user_request="调研压缩方法", extra={"run_root": str(tmp_path)})
        with pytest.raises(LiteratureAccessError) as failure:
            await agent.build_context(request)
        assert failure.value.reason["code"] == code
        assert not (tmp_path / "agent_traces").exists()
        assert not (tmp_path / "input/idea_focused.v1.json").exists()
    finally:
        reset_settings_cache()
