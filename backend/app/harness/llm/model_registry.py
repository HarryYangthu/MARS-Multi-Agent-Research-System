"""Routes (provider, model) pairs to concrete LLMProvider instances.

★ Critical fallback rule (DESIGN §16.1): if the requested provider has no
API key (or its endpoint is unreachable), the registry returns a
``MockProvider`` — and logs a clear warning so the run still completes.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from app.harness.llm.mock_provider import MockProvider
from app.harness.llm.provider_base import LLMConfig, LLMProvider
from app.settings import env_or_local, get_settings, repo_root


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
    api_key_env: str = ""
    base_url: str = ""
    base_url_env: str = ""


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        return {}
    return data


@lru_cache(maxsize=1)
def _models_config() -> dict[str, Mapping[str, Any]]:
    cfg_path = repo_root() / "configs" / "models.yaml"
    raw = _load_yaml(cfg_path)
    providers = raw.get("providers", {})
    if not isinstance(providers, dict):
        return {}
    return {
        str(name): body
        for name, body in providers.items()
        if isinstance(body, Mapping)
    }


def _provider_defaults(provider: str) -> Mapping[str, Any]:
    return _models_config().get(provider, {})


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
        provider = str(model.get("provider", "mock"))
        provider_defaults = _provider_defaults(provider)
        out[name] = AgentConfig(
            name=name,
            enabled=bool(body.get("enabled", True)),
            output_schema=str(body.get("output_schema", "")),
            model_provider=provider,
            model_name=str(model.get("model", "mock-1")),
            temperature=float(model.get("temperature", 0.7)),
            max_tokens=int(model.get("max_tokens", 4096)),
            debate_enabled=bool(debate.get("enabled", False)),
            debate_rounds=int(debate.get("rounds", 1)),
            debate_participants=tuple(debate.get("participants", []) or []),
            tools=tuple(body.get("tools", []) or []),
            raw=body,
            api_key_env=str(
                model.get("api_key_env")
                or provider_defaults.get("api_key_env")
                or ""
            ),
            base_url=str(
                model.get("base_url") or provider_defaults.get("base_url") or ""
            ),
            base_url_env=str(
                model.get("base_url_env")
                or provider_defaults.get("base_url_env")
                or ""
            ),
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

_KEY_ATTRS: Mapping[str, str] = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "qwen": "qwen_api_key",
    "gemini": "gemini_api_key",
    "deepseek": "deepseek_api_key",
    "local_vllm": "local_vllm_api_key",
    "custom": "custom_endpoint_api_key",
}

_BASE_URL_ATTRS: Mapping[str, str] = {
    "deepseek": "deepseek_base_url",
    "local_vllm": "local_vllm_base_url",
    "custom": "custom_endpoint_url",
}


def _settings_value(settings: Any, attr: str) -> str:
    val = getattr(settings, attr, "")
    return str(val or "")


def available_providers(*, include_mock: bool = True) -> set[str]:
    """Set of providers whose API key (or endpoint) is configured."""
    out: set[str] = set()
    if _secret_value("ANTHROPIC_API_KEY", "anthropic_api_key"):
        out.add("anthropic")
    if _secret_value("OPENAI_API_KEY", "openai_api_key"):
        out.add("openai")
    if _secret_value("QWEN_API_KEY", "qwen_api_key"):
        out.add("qwen")
    if _secret_value("GEMINI_API_KEY", "gemini_api_key"):
        out.add("gemini")
    if _secret_value("LOCAL_VLLM_BASE_URL", "local_vllm_base_url"):
        out.add("local_vllm")
    if _secret_value("CUSTOM_ENDPOINT_URL", "custom_endpoint_url") and _secret_value(
        "CUSTOM_ENDPOINT_API_KEY", "custom_endpoint_api_key"
    ):
        out.add("custom")
    if _secret_value("DEEPSEEK_API_KEY", "deepseek_api_key"):
        out.add("deepseek")
    if include_mock:
        out.add("mock")  # always available outside production admission checks
    return out


def provider_configured_for_agent(agent_config: AgentConfig) -> bool:
    return _provider_is_ready(
        agent_config.model_provider,
        _provider_credentials(agent_config),
    )


def _build_real_provider(
    provider: str,
    *,
    agent_config: AgentConfig | None = None,
    api_key_env: str = "",
    base_url: str = "",
    base_url_env: str = "",
) -> LLMProvider | None:
    settings = get_settings()
    credentials = _provider_credentials(
        agent_config,
        provider=provider,
        api_key_env=api_key_env,
        base_url=base_url,
        base_url_env=base_url_env,
    )
    try:
        if provider == "anthropic":
            from app.harness.llm.anthropic_provider import AnthropicProvider

            return AnthropicProvider(api_key=credentials["api_key"])
        if provider == "openai":
            from app.harness.llm.openai_provider import OpenAIProvider

            return OpenAIProvider(api_key=credentials["api_key"])
        if provider == "qwen":
            from app.harness.llm.openai_provider import QwenProvider

            return QwenProvider(
                api_key=credentials["api_key"],
                base_url=credentials["base_url"]
                or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
        if provider == "gemini":
            from app.harness.llm.gemini_provider import GeminiProvider

            return GeminiProvider(api_key=credentials["api_key"])
        if provider == "local_vllm":
            from app.harness.llm.openai_provider import LocalVllmProvider

            return LocalVllmProvider(
                base_url=credentials["base_url"],
                api_key=credentials["api_key"] or "EMPTY",
            )
        if provider == "custom":
            from app.harness.llm.openai_provider import CustomEndpointProvider

            return CustomEndpointProvider(
                api_key=credentials["api_key"],
                base_url=credentials["base_url"],
            )
        if provider == "deepseek":
            from app.harness.llm.openai_provider import DeepSeekProvider

            return DeepSeekProvider(
                api_key=credentials["api_key"],
                base_url=credentials["base_url"] or settings.deepseek_base_url,
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

    if not provider_configured_for_agent(agent_config):
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

    real = _build_real_provider(agent_config.model_provider, agent_config=agent_config)
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
    _models_config.cache_clear()
    # Also clear settings cache so env changes take effect
    from app.settings import reset_settings_cache

    reset_settings_cache()


def _secret_value(env_name: str, settings_attr: str) -> str:
    return env_or_local(env_name) or _settings_value(get_settings(), settings_attr)


def _provider_credentials(
    agent_config: AgentConfig | None,
    *,
    provider: str | None = None,
    api_key_env: str = "",
    base_url: str = "",
    base_url_env: str = "",
) -> dict[str, str]:
    selected_provider = provider or (agent_config.model_provider if agent_config else "")
    defaults = _provider_defaults(selected_provider)
    agent_defaults_apply = (
        agent_config is not None and agent_config.model_provider == selected_provider
    )
    agent_key_env = agent_config.api_key_env if agent_defaults_apply and agent_config else ""
    agent_base_url_env = (
        agent_config.base_url_env if agent_defaults_apply and agent_config else ""
    )
    agent_base_url = agent_config.base_url if agent_defaults_apply and agent_config else ""
    resolved_key_env = (
        api_key_env
        or agent_key_env
        or str(defaults.get("api_key_env") or "")
    )
    resolved_base_url_env = (
        base_url_env
        or agent_base_url_env
        or str(defaults.get("base_url_env") or "")
    )
    resolved_base_url = (
        base_url
        or agent_base_url
        or str(defaults.get("base_url") or "")
    )
    key_attr = _KEY_ATTRS.get(selected_provider, "")
    base_url_attr = _BASE_URL_ATTRS.get(selected_provider, "")
    api_key = env_or_local(resolved_key_env)
    if not api_key and key_attr:
        api_key = _settings_value(get_settings(), key_attr)
    actual_base_url = env_or_local(resolved_base_url_env) or resolved_base_url
    if not actual_base_url and base_url_attr:
        actual_base_url = _settings_value(get_settings(), base_url_attr)
    return {
        "api_key": api_key,
        "api_key_env": resolved_key_env,
        "base_url": actual_base_url,
        "base_url_env": resolved_base_url_env,
    }


def _provider_is_ready(provider: str, credentials: Mapping[str, str]) -> bool:
    if provider == "mock":
        return True
    if provider == "local_vllm":
        return bool(credentials.get("base_url"))
    if provider == "custom":
        return bool(credentials.get("api_key") and credentials.get("base_url"))
    if provider in {"anthropic", "openai", "qwen", "gemini", "deepseek"}:
        return bool(credentials.get("api_key"))
    return False
