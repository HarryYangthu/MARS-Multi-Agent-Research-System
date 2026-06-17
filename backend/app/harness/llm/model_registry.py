"""Routes (provider, model) pairs to concrete LLMProvider instances.

★ Critical fallback rule (DESIGN §16.1): if the requested provider has no
API key (or its endpoint is unreachable), the registry returns a
``MockProvider`` — and logs a clear warning so the run still completes.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from app.harness.llm.mock_provider import MockProvider
from app.harness.llm.provider_base import LLMConfig, LLMProvider
from app.settings import get_settings, repo_root


@dataclass(frozen=True)
class AgentConfig:
    name: str
    enabled: bool
    output_schema: str
    model_provider: str
    model_name: str
    temperature: float
    max_tokens: int
    debate_enabled: bool
    debate_rounds: int
    debate_participants: tuple[Mapping[str, Any], ...]
    tools: tuple[str, ...]
    raw: Mapping[str, Any]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        return {}
    return data


@lru_cache(maxsize=1)
def _agents_config() -> dict[str, AgentConfig]:
    cfg_path = repo_root() / "configs" / "agents.yaml"
    raw = _load_yaml(cfg_path)
    out: dict[str, AgentConfig] = {}
    for name, body in raw.items():
        if not isinstance(body, dict):
            continue
        model = body.get("model", {}) or {}
        debate = body.get("debate", {}) or {}
        out[name] = AgentConfig(
            name=name,
            enabled=bool(body.get("enabled", True)),
            output_schema=str(body.get("output_schema", "")),
            model_provider=str(model.get("provider", "mock")),
            model_name=str(model.get("model", "mock-1")),
            temperature=float(model.get("temperature", 0.7)),
            max_tokens=int(model.get("max_tokens", 4096)),
            debate_enabled=bool(debate.get("enabled", False)),
            debate_rounds=int(debate.get("rounds", 1)),
            debate_participants=tuple(debate.get("participants", []) or []),
            tools=tuple(body.get("tools", []) or []),
            raw=body,
        )
    return out


def get_agent_config(name: str) -> AgentConfig:
    cfgs = _agents_config()
    if name not in cfgs:
        raise KeyError(f"no agent config named '{name}' in configs/agents.yaml")
    return cfgs[name]


def list_agent_configs() -> list[AgentConfig]:
    return list(_agents_config().values())


# ----------------------------------------------------------------- providers


def _settings_value(settings: Any, attr: str) -> str:
    val = getattr(settings, attr, "")
    return str(val or "")


def available_providers(*, include_mock: bool = True) -> set[str]:
    """Set of providers whose API key (or endpoint) is configured."""
    settings = get_settings()
    out: set[str] = set()
    if _settings_value(settings, "anthropic_api_key"):
        out.add("anthropic")
    if _settings_value(settings, "openai_api_key"):
        out.add("openai")
    if _settings_value(settings, "qwen_api_key"):
        out.add("qwen")
    if _settings_value(settings, "gemini_api_key"):
        out.add("gemini")
    if _settings_value(settings, "local_vllm_base_url"):
        out.add("local_vllm")
    if _settings_value(settings, "custom_endpoint_url") and _settings_value(
        settings, "custom_endpoint_api_key"
    ):
        out.add("custom")
    if _settings_value(settings, "deepseek_api_key"):
        out.add("deepseek")
    if include_mock:
        out.add("mock")  # always available outside production admission checks
    return out


def _build_real_provider(provider: str) -> LLMProvider | None:
    settings = get_settings()
    try:
        if provider == "anthropic":
            from app.harness.llm.anthropic_provider import AnthropicProvider

            return AnthropicProvider(api_key=settings.anthropic_api_key)
        if provider == "openai":
            from app.harness.llm.openai_provider import OpenAIProvider

            return OpenAIProvider(api_key=settings.openai_api_key)
        if provider == "qwen":
            from app.harness.llm.openai_provider import QwenProvider

            return QwenProvider(api_key=settings.qwen_api_key)
        if provider == "gemini":
            from app.harness.llm.gemini_provider import GeminiProvider

            return GeminiProvider(api_key=settings.gemini_api_key)
        if provider == "local_vllm":
            from app.harness.llm.openai_provider import LocalVllmProvider

            return LocalVllmProvider(
                base_url=settings.local_vllm_base_url,
                api_key=settings.local_vllm_api_key,
            )
        if provider == "custom":
            from app.harness.llm.openai_provider import CustomEndpointProvider

            return CustomEndpointProvider(
                api_key=settings.custom_endpoint_api_key,
                base_url=settings.custom_endpoint_url,
            )
        if provider == "deepseek":
            from app.harness.llm.openai_provider import DeepSeekProvider

            return DeepSeekProvider(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
            )
    except Exception as exc:
        logger.warning(
            "failed to build provider '{}': {}; falling back to mock",
            provider,
            exc,
        )
        return None
    return None


def select_provider(agent_config: AgentConfig) -> tuple[LLMProvider, LLMConfig]:
    """Pick a provider for an agent, with mock fallback.

    Returns (provider, llm_config). The llm_config carries the agent's
    output_schema so MockProvider can produce the right fake.
    """
    settings = get_settings()
    cfg = LLMConfig(
        provider=agent_config.model_provider,
        model=agent_config.model_name,
        temperature=agent_config.temperature,
        max_tokens=agent_config.max_tokens,
        response_schema=agent_config.output_schema,
    )

    if settings.mars_mock_mode == "always":
        if settings.is_production:
            raise RuntimeError("production mode cannot use MARS_MOCK_MODE=always")
        logger.info("MARS_MOCK_MODE=always — agent {} uses mock", agent_config.name)
        return MockProvider(default_schema=agent_config.output_schema), cfg

    avail = available_providers()
    if agent_config.model_provider not in avail:
        if settings.is_production or settings.mars_mock_mode == "never":
            raise RuntimeError(
                f"provider '{agent_config.model_provider}' is not configured "
                f"for agent '{agent_config.name}'"
            )
        logger.warning(
            "provider '{}' not configured (agent={}) — falling back to mock_provider",
            agent_config.model_provider,
            agent_config.name,
        )
        return MockProvider(default_schema=agent_config.output_schema), cfg

    real = _build_real_provider(agent_config.model_provider)
    if real is None:
        if settings.is_production or settings.mars_mock_mode == "never":
            raise RuntimeError(
                f"provider '{agent_config.model_provider}' failed to initialize "
                f"for agent '{agent_config.name}'"
            )
        return MockProvider(default_schema=agent_config.output_schema), cfg
    return real, cfg


def reset_cache_for_tests() -> None:
    _agents_config.cache_clear()
    # Also clear settings cache so env changes take effect
    from app.settings import _settings  # noqa: PLC0415  (test helper)

    if _settings is not None:
        # Reset the cached settings instance
        import app.settings as _settings_mod

        _settings_mod._settings = None
