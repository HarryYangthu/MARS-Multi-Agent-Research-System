"""Shared pytest fixtures for the MARS backend test suite.

Provider availability must be deterministic. Without this, tests depend on
whatever the developer happens to have in their local ``.env`` — e.g. a
``LOCAL_VLLM_BASE_URL`` makes ``local_vllm`` "available" and silently flips
``debate_runner._auto_mode`` from ``mock_debate`` to ``single_model_simulated``.

The autouse fixture below neutralises every LLM-provider env var (overriding
any ``.env`` value, since OS env vars take precedence over the dotenv file)
and resets the cached ``Settings`` instance before and after each test. A test
opts into a provider by ``monkeypatch.setenv``-ing its key afterwards.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

# Every env var that influences ``available_providers()``.
_PROVIDER_ENV: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "QWEN_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "LOCAL_VLLM_BASE_URL",
    "CUSTOM_ENDPOINT_URL",
    "CUSTOM_ENDPOINT_API_KEY",
)


@pytest.fixture(autouse=True)
def _hermetic_providers(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start every test with no providers configured and a fresh settings cache."""
    import app.settings as settings_mod

    # Empty string overrides any value in .env (env > dotenv in pydantic-settings)
    # while still being falsy for the truthiness checks in available_providers().
    for env in _PROVIDER_ENV:
        monkeypatch.setenv(env, "")
    settings_mod._settings = None
    yield
    settings_mod._settings = None
