"""Verify debate auto-degrade across the three modes."""
from __future__ import annotations

import pytest

from app.agents.base import ContextPack, RunRequest
from app.agents.debate.debate_runner import (
    DebateMode,
    _auto_mode,
    run_debate,
)
from app.harness.llm.model_registry import get_agent_config


def test_auto_mode_no_keys_returns_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "QWEN_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = get_agent_config("idea")
    mode = _auto_mode(cfg)
    assert mode == DebateMode.MOCK_DEBATE


def test_auto_mode_partial_keys_simulates(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in ("OPENAI_API_KEY", "QWEN_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = get_agent_config("idea")  # demands anthropic + openai + gemini
    mode = _auto_mode(cfg)
    assert mode == DebateMode.SINGLE_MODEL_SIMULATED


def test_auto_mode_all_keys_real(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = get_agent_config("idea")
    mode = _auto_mode(cfg)
    assert mode == DebateMode.REAL_MULTI_MODEL


@pytest.mark.asyncio
async def test_run_debate_mock_mode_produces_valid_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "QWEN_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = get_agent_config("idea")
    request = RunRequest(project="moe-pimc", user_request="test")
    context = ContextPack(
        system="system text", project="project text", task="task text"
    )
    result = await run_debate(
        agent_name="idea",
        agent_config=cfg,
        request=request,
        context=context,
        output_schema="proposal.v1",
    )
    assert result.mode == DebateMode.MOCK_DEBATE
    assert result.final_artifact is not None
    # validate the synthesized artifact
    from app.harness.schema.validator import validate_document

    res = validate_document(
        result.final_artifact.text, expected_schema="proposal.v1"
    )
    assert res.valid, res.errors
