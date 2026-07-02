"""Verify debate auto-degrade across the three modes."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.agents.base import ContextPack, RunRequest
from app.agents.debate.debate_runner import (
    DebateMode,
    _auto_mode,
    run_debate,
)
from app.harness.llm.model_registry import get_agent_config

ALL_LLM_KEY_ENVS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "QWEN_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
)


def test_auto_mode_no_keys_returns_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in ALL_LLM_KEY_ENVS:
        monkeypatch.setenv(env, "")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "auto")
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = get_agent_config("idea")
    mode = _auto_mode(cfg)
    assert mode == DebateMode.MOCK_DEBATE


def test_auto_mode_never_rejects_missing_debate_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env in ALL_LLM_KEY_ENVS:
        monkeypatch.setenv(env, "")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "staging")
    monkeypatch.setenv("MARS_MOCK_MODE", "never")
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = get_agent_config("idea")
    with pytest.raises(RuntimeError, match="debate provider"):
        _auto_mode(cfg)


def test_auto_mode_partial_keys_simulates(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in ALL_LLM_KEY_ENVS:
        monkeypatch.setenv(env, "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "auto")
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = replace(
        get_agent_config("idea"),
        debate_participants=(
            {"role": "proposer", "provider": "deepseek", "model": "deepseek-chat"},
            {"role": "critic", "provider": "openai", "model": "gpt-test"},
        ),
    )
    mode = _auto_mode(cfg)
    assert mode == DebateMode.SINGLE_MODEL_SIMULATED


def test_auto_mode_all_keys_real(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "auto")
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = get_agent_config("idea")
    mode = _auto_mode(cfg)
    assert mode == DebateMode.REAL_MULTI_MODEL


@pytest.mark.asyncio
async def test_run_debate_mock_mode_produces_valid_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env in ALL_LLM_KEY_ENVS:
        monkeypatch.setenv(env, "")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "auto")
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = get_agent_config("idea")
    request = RunRequest(project="pimc", user_request="test")
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


@pytest.mark.asyncio
async def test_run_debate_writes_precall_manifests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for env in ALL_LLM_KEY_ENVS:
        monkeypatch.setenv(env, "")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "auto")
    import app.settings as settings_mod

    settings_mod._settings = None
    cfg = replace(get_agent_config("idea"), debate_rounds=1)
    run_root = tmp_path / "run"
    request = RunRequest(
        project="pimc",
        user_request="debate manifest",
        extra={"run_id": "run-debate", "run_root": str(run_root), "node_key": "idea"},
    )
    context = ContextPack(
        system="system text", project="project text", task="task text"
    )

    result = await run_debate(
        agent_name="idea",
        agent_config=cfg,
        request=request,
        context=context,
        output_schema="proposal.v1",
        mode=DebateMode.MOCK_DEBATE,
    )

    assert result.final_artifact is not None
    manifests = []
    for path in sorted((run_root / "context").glob("context_manifest.v2.*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            manifests.append(raw)
    purposes = {item["purpose"] for item in manifests}
    assert {
        "debate_proposer_round_1",
        "debate_critic_round_1",
        "debate_judge_round_1",
    }.issubset(purposes)
    assert all(item["diagnostics"].get("capture_mode") == "messages" for item in manifests)
