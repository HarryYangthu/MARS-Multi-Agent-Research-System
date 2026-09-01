from __future__ import annotations

import pytest

from app.harness.llm.mock_provider import MockProvider
from app.harness.llm.model_registry import (
    available_providers,
    get_agent_config,
    list_agent_configs,
    select_provider,
)


def test_agent_configs_loaded_from_yaml() -> None:
    cfgs = list_agent_configs()
    names = {c.name for c in cfgs}
    assert {"idea", "experiment", "coding", "execution", "writing"}.issubset(names)


def test_idea_config_has_debate_participants() -> None:
    cfg = get_agent_config("idea")
    assert cfg.debate_enabled is True
    assert len(cfg.debate_participants) >= 2
    assert cfg.output_schema == "proposal.v1"
    assert cfg.model_name == "deepseek-v4-pro"
    assert cfg.thinking_enabled is True
    assert cfg.reasoning_effort == "high"
    assert cfg.max_tokens == 16_384
    assert cfg.top_p == 1.0
    assert cfg.request_timeout_seconds == 120.0
    assert cfg.max_retries == 3


def test_all_enabled_agents_use_the_deepseek_research_profile() -> None:
    for cfg in list_agent_configs():
        if not cfg.enabled:
            continue
        assert cfg.model_provider == "deepseek"
        assert cfg.model_name == "deepseek-v4-pro"
        assert cfg.thinking_enabled is True
        assert cfg.reasoning_effort == "high"
        assert cfg.max_tokens >= 16_384
        if cfg.name in {"coding", "writing"}:
            assert cfg.max_tokens >= 32_768


def test_select_provider_falls_back_to_mock_without_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force absence of all keys.
    for env in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "QWEN_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.setenv(env, "")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "auto")
    from app.settings import _settings  # noqa
    import app.settings as settings_mod

    settings_mod._settings = None  # invalidate cache
    cfg = get_agent_config("idea")
    provider, llm_cfg = select_provider(cfg)
    assert isinstance(provider, MockProvider)
    assert llm_cfg.response_schema == "proposal.v1"
    assert llm_cfg.thinking_enabled is True
    assert llm_cfg.reasoning_effort == "high"
    assert llm_cfg.max_tokens == 16_384
    assert llm_cfg.top_p == 1.0
    assert llm_cfg.request_timeout_seconds == 120.0
    assert llm_cfg.max_retries == 3


def test_available_providers_always_includes_mock() -> None:
    assert "mock" in available_providers()


def test_select_provider_rejects_mock_fallback_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "QWEN_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "LOCAL_VLLM_BASE_URL",
    ):
        monkeypatch.setenv(env, "")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "production")
    monkeypatch.setenv("MARS_MOCK_MODE", "never")
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = get_agent_config("idea")
    with pytest.raises(RuntimeError, match="not configured"):
        select_provider(cfg)
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    settings_mod._settings = None
