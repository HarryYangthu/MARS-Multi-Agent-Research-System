"""Verify debate auto-degrade across the three modes.

These use a synthetic debate-enabled AgentConfig so they exercise the
_auto_mode logic independent of the live agents.yaml (which may have debate
disabled for demo pacing). The conftest fixture clears all provider env.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from app.agents.base import ContextPack, RunRequest
from app.agents.debate.debate_runner import (
    DebateMode,
    _auto_mode,
    run_debate,
)
from app.harness.llm.model_registry import AgentConfig


def _debate_cfg(participants: Sequence[Mapping[str, Any]]) -> AgentConfig:
    return AgentConfig(
        name="idea",
        enabled=True,
        output_schema="proposal.v1",
        model_provider="deepseek",
        model_name="deepseek-reasoner",
        temperature=0.7,
        max_tokens=4096,
        debate_enabled=True,
        debate_rounds=1,
        debate_participants=tuple(participants),
        tools=(),
        raw={},
    )


_DEEPSEEK_PARTICIPANTS = [
    {"role": "proposer", "provider": "deepseek", "model": "deepseek-reasoner"},
    {"role": "critic", "provider": "deepseek", "model": "deepseek-reasoner"},
    {"role": "judge", "provider": "deepseek", "model": "deepseek-reasoner"},
]


def _reset_settings() -> None:
    import app.settings as settings_mod

    settings_mod._settings = None


def test_auto_mode_no_keys_returns_mock() -> None:
    _reset_settings()
    assert _auto_mode(_debate_cfg(_DEEPSEEK_PARTICIPANTS)) == DebateMode.MOCK_DEBATE


def test_auto_mode_partial_keys_simulates(monkeypatch: pytest.MonkeyPatch) -> None:
    # A key exists, but not for the deepseek participants → simulate.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    _reset_settings()
    mode = _auto_mode(_debate_cfg(_DEEPSEEK_PARTICIPANTS))
    assert mode == DebateMode.SINGLE_MODEL_SIMULATED


def test_auto_mode_all_keys_real(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    _reset_settings()
    mode = _auto_mode(_debate_cfg(_DEEPSEEK_PARTICIPANTS))
    assert mode == DebateMode.REAL_MULTI_MODEL


def test_auto_mode_debate_disabled_is_mock() -> None:
    cfg = _debate_cfg(_DEEPSEEK_PARTICIPANTS)
    disabled = AgentConfig(**{**cfg.__dict__, "debate_enabled": False})
    _reset_settings()
    assert _auto_mode(disabled) == DebateMode.MOCK_DEBATE


@pytest.mark.asyncio
async def test_run_debate_mock_mode_produces_valid_artifact() -> None:
    _reset_settings()
    cfg = _debate_cfg(_DEEPSEEK_PARTICIPANTS)
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
    from app.harness.schema.validator import validate_document

    res = validate_document(result.final_artifact.text, expected_schema="proposal.v1")
    assert res.valid, res.errors
