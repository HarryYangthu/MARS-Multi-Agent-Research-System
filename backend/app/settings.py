"""Centralized settings loaded from environment.

Development keeps the V0 mock-first defaults. Production mode is fail-closed:
missing LLM or execution configuration must stop a run instead of silently
falling back to demo behavior.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
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

    # === Mode flags ===
    mars_runtime_mode: Literal["development", "staging", "production"] = "development"
    mars_mock_mode: Literal["auto", "always", "never"] = "auto"
    mars_execution_backend: Literal[
        "mock",
        "pim_cpu",
        "local_command",
        "docker_command",
        "remote_gpu",
    ] = "mock"
    mars_log_level: str = "INFO"
    mars_default_project: str = "moe-pimc"
    mars_llm_timeout_seconds: float = 15.0
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


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def repo_root() -> Path:
    return REPO_ROOT
