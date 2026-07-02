from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.harness.runtime.readiness import check_readiness


@pytest.fixture(autouse=True)
def _reset_settings() -> Iterator[None]:
    import app.settings as settings_mod

    settings_mod._settings = None
    yield
    settings_mod._settings = None


def test_development_readiness_allows_mock_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "mock")
    report = check_readiness(project="pimc")
    assert report.runtime_mode == "development"
    assert any(c.name == "execution_backend" and c.ready for c in report.checks)


def test_production_readiness_blocks_missing_llm_and_mock_execution(
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
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "mock")
    import app.settings as settings_mod

    settings_mod._settings = None
    report = check_readiness(project="pimc")
    blockers = {
        c.name for c in report.checks if c.severity == "blocker" and not c.ready
    }
    assert not report.ready
    assert {"llm_providers", "execution_backend"}.issubset(blockers)


def test_mock_never_blocks_missing_llm_in_development(
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
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "never")
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "paper_static")
    import app.settings as settings_mod

    settings_mod._settings = None
    report = check_readiness(project="pimc")
    blockers = {
        c.name for c in report.checks if c.severity == "blocker" and not c.ready
    }
    assert not report.ready
    assert "llm_providers" in blockers
