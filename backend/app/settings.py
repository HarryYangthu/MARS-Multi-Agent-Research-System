"""Centralized settings loaded from environment.

Development keeps the V0 mock-first defaults. Production mode is fail-closed:
missing LLM or execution configuration must stop a run instead of silently
falling back to demo behavior.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Mapping

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ENV_FILES = (REPO_ROOT / ".env", REPO_ROOT / ".env.local")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=tuple(str(path) for path in LOCAL_ENV_FILES),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # === LLM keys (all optional) ===
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    qwen_api_key: str = ""
    gemini_api_key: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # Default empty — local_vllm is "available" only when explicitly configured.
    local_vllm_base_url: str = ""
    local_vllm_api_key: str = "EMPTY"

    custom_endpoint_url: str = ""
    custom_endpoint_api_key: str = ""

    # === Infra ===
    redis_url: str = "redis://localhost:6379/0"
    chromadb_path: str = str(REPO_ROOT / "knowledge" / ".chromadb")

    # === Service ===
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    frontend_port: int = 3000
    mars_cors_origins: str = "*"

    # === Mode flags ===
    mars_runtime_mode: Literal["development", "staging", "production"] = "development"
    mars_mock_mode: Literal["auto", "always", "never"] = "auto"
    mars_graph_engine: Literal["langgraph", "legacy"] = "langgraph"
    mars_execution_backend: Literal[
        "mock",
        "pim_cpu",
        "paper_static",
        "local_command",
        "docker_command",
        "remote_gpu",
    ] = "mock"
    mars_coding_backend: Literal[
        "mock",
        "native_llm",
        "opencode",
        "codex",
        "claude_code",
    ] = "opencode"
    mars_log_level: str = "INFO"
    mars_default_project: str = "pimc"
    mars_llm_timeout_seconds: float = 90.0
    mars_enable_network_tools: bool = False
    mars_web_search_allowlist: str = ""
    mars_web_search_provider: Literal["", "brave", "tavily", "serper"] = ""
    brave_search_api_key: str = ""
    tavily_api_key: str = ""
    serper_api_key: str = ""
    mars_context_max_tokens: int = 32_000
    mars_context_target_tokens: int = 24_000
    mars_context_auto_compress: bool = True
    mars_context_tool_raw_externalize: bool = True
    mars_context_workbench_enabled: bool = True

    # === Optional observability sinks ===
    # LangSmith is an external mirror only; file-backed traces remain mandatory.
    mars_langsmith_enabled: bool = False
    langsmith_api_key: str = ""
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_project: str = "mars-dev"
    mars_langsmith_timeout_ms: int = 1000

    @property
    def is_production(self) -> bool:
        return self.mars_runtime_mode == "production"

    @property
    def mock_allowed(self) -> bool:
        return not self.is_production and self.mars_mock_mode != "never"

    @property
    def cors_origins(self) -> list[str]:
        raw = self.mars_cors_origins.strip()
        if not raw or raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    global _settings
    _settings = None


def local_env_files() -> tuple[Path, ...]:
    return LOCAL_ENV_FILES


def read_local_env_vars() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in LOCAL_ENV_FILES:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            parsed = _parse_env_line(raw_line)
            if parsed is None:
                continue
            key, value = parsed
            values[key] = value
    return values


def env_or_local(name: str) -> str:
    key = name.strip()
    if not key:
        return ""
    runtime_value = os.environ.get(key)
    if runtime_value is not None:
        return runtime_value
    return read_local_env_vars().get(key, "")


def set_runtime_env(values: Mapping[str, str]) -> None:
    for key, value in values.items():
        if key:
            os.environ[key] = value
    reset_settings_cache()


def repo_root() -> Path:
    return REPO_ROOT


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    return key, _strip_env_value(value.strip())


def _strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
