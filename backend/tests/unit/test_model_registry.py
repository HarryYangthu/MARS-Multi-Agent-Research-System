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
    # debate.enabled is a demo-tunable toggle; participants stay declared.
    assert len(cfg.debate_participants) >= 2
    assert cfg.output_schema == "proposal.v1"


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
