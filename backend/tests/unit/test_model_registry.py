from __future__ import annotations

from dataclasses import replace

import pytest

from app.harness.llm.model_registry import (
    available_providers,
    get_agent_config,
    list_agent_configs,
    select_provider,
    reset_cache_for_tests,
)


def test_agent_configs_loaded_from_yaml() -> None:
    cfgs = list_agent_configs()
    names = {c.name for c in cfgs}
    assert {"idea", "experiment", "coding", "execution", "writing"}.issubset(names)


def test_idea_config_has_debate_participants() -> None:
    cfg = get_agent_config("idea")
    assert cfg.debate_enabled is False
    assert len(cfg.debate_participants) >= 2
    assert cfg.output_schema == "proposal.v1"
    assert cfg.model_name == ("deepseek-v4-flash" if cfg.name == "idea" else "deepseek-v4-pro")
    assert cfg.thinking_enabled is (cfg.name != "idea")
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
        assert cfg.model_name == ("deepseek-v4-flash" if cfg.name == "idea" else "deepseek-v4-pro")
        assert cfg.thinking_enabled is (cfg.name != "idea")
        assert cfg.reasoning_effort == "high"
        assert cfg.max_tokens >= 16_384
        if cfg.name in {"coding", "writing"}:
            assert cfg.max_tokens >= 32_768


def test_local_provider_selection_preserves_agent_configuration() -> None:
    # Construct the real adapter; this does not assert endpoint connectivity.
    cfg = replace(get_agent_config("idea"), model_provider="local_vllm",
                  base_url="http://127.0.0.1:1/v1", base_url_env="MARS_TEST_UNUSED_ENDPOINT",
                  api_key_env="MARS_TEST_UNUSED_KEY")
    provider, llm_cfg = select_provider(cfg)
    assert provider.name == "local_vllm"
    assert llm_cfg.response_schema == "proposal.v1"
    assert llm_cfg.thinking_enabled == cfg.thinking_enabled
    assert llm_cfg.reasoning_effort == "high"
    assert llm_cfg.max_tokens == 16_384
    assert llm_cfg.top_p == 1.0
    assert llm_cfg.request_timeout_seconds == 120.0
    assert llm_cfg.max_retries == 3


def test_available_providers_excludes_removed_simulated_provider() -> None:
    assert "mock" not in available_providers()


@pytest.mark.parametrize("runtime", ["production", "development"])
def test_missing_explicit_provider_fails_in_every_runtime(
    monkeypatch: pytest.MonkeyPatch, runtime: str,
) -> None:
    monkeypatch.setenv("MARS_RUNTIME_MODE", runtime)
    monkeypatch.setenv("MARS_MOCK_MODE", "never")
    reset_cache_for_tests()
    # An unsupported explicit provider cannot be replaced by any configured one.
    cfg = replace(get_agent_config("idea"), model_provider="unconfigured")
    try:
        with pytest.raises(RuntimeError, match="not configured"):
            select_provider(cfg)
    finally:
        reset_cache_for_tests()
