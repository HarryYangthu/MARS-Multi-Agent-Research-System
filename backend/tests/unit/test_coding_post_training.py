from __future__ import annotations

import pytest

from app.agents.coding.agent import CodingAgent
from app.harness.llm.model_registry import AgentConfig


def _agent_config(post_training: dict[str, object]) -> AgentConfig:
    return AgentConfig(
        name="coding",
        enabled=True,
        output_schema="code_spec.v1",
        model_provider="deepseek",
        model_name="deepseek-chat",
        temperature=0.1,
        max_tokens=8192,
        debate_enabled=False,
        debate_rounds=1,
        debate_participants=(),
        tools=(),
        raw={"post_training": post_training},
    )


def test_disabled_post_training_uses_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    import app.settings as settings_mod

    settings_mod._settings = None
    agent = CodingAgent(
        agent_config=_agent_config({"enabled": False, "mode": "load_only"})
    )
    _, llm_cfg = agent._select_provider()
    assert llm_cfg.provider == "deepseek"
    assert llm_cfg.model == "deepseek-chat"


def test_endpoint_post_training_overrides_coding_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_MOCK_MODE", "auto")
    monkeypatch.setenv("LOCAL_VLLM_API_KEY", "EMPTY")
    import app.settings as settings_mod

    settings_mod._settings = None
    agent = CodingAgent(
        agent_config=_agent_config(
            {
                "enabled": True,
                "mode": "endpoint",
                "endpoint_provider": "local_vllm",
                "custom_endpoint": "http://127.0.0.1:8001/v1",
                "model": "mars-coding-posttrain",
                "api_key_env": "LOCAL_VLLM_API_KEY",
            }
        )
    )

    provider, llm_cfg = agent._select_provider()

    assert provider.name == "local_vllm"
    assert llm_cfg.provider == "local_vllm"
    assert llm_cfg.model == "mars-coding-posttrain"
    assert llm_cfg.response_schema == "code_spec.v1"
    assert llm_cfg.extra["post_training"]["source"] == "config"
