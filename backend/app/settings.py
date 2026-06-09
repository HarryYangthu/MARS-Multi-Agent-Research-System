"""Centralized settings loaded from environment.

All LLM keys are optional. Missing keys trigger fallback to mock_provider.
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
    mars_mock_mode: Literal["auto", "always", "never"] = "auto"
    mars_log_level: str = "INFO"
    mars_default_project: str = "moe-pimc"
    # Natural-language for Agent outputs (body + free-text field values).
    # Schema keys / consts / enum values always stay English regardless.
    mars_agent_language: Literal["zh", "en"] = "zh"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def repo_root() -> Path:
    return REPO_ROOT
