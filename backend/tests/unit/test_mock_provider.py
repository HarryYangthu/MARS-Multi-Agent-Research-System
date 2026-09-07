"""Regression guard: removed fake providers cannot be imported or selected."""
from __future__ import annotations

import importlib.util
from dataclasses import replace

import pytest

from app.harness.llm.model_registry import available_providers, get_agent_config, select_provider
from app.settings import Settings


def test_removed_provider_is_not_importable() -> None:
    assert importlib.util.find_spec("app.harness.llm.mock_provider") is None


def test_provider_catalogue_never_advertises_mock() -> None:
    assert "mock" not in available_providers(include_mock=True)


@pytest.mark.parametrize("mode", ["auto", "always"])
def test_legacy_fallback_modes_fail_configuration(mode: str) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, mars_mock_mode=mode)


def test_requesting_mock_cannot_return_generated_sample() -> None:
    config = replace(get_agent_config("idea"), model_provider="mock", api_key_env="", base_url="")
    with pytest.raises(RuntimeError):
        select_provider(config)
