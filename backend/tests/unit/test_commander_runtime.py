"""Commander configuration and decision parsing; actual network failures are tested separately."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.bridge.commander import DEFAULT_MAX_REACT_STEPS, Commander, _parse_decision, _react_step_limit
from app.bridge.orchestrator import Orchestrator
from app.harness.llm import model_registry
from app.harness.llm.model_registry import get_agent_config
from app.storage.run_store import RunStore


@pytest.mark.parametrize("raw,expected", [
    ({}, DEFAULT_MAX_REACT_STEPS), ({"loop": "invalid"}, DEFAULT_MAX_REACT_STEPS),
    ({"loop": {"max_tool_steps": True}}, DEFAULT_MAX_REACT_STEPS),
    ({"loop": {"max_tool_steps": "invalid"}}, DEFAULT_MAX_REACT_STEPS),
    ({"loop": {"max_tool_steps": 0}}, 1), ({"loop": {"max_tool_steps": "8"}}, 8),
    ({"loop": {"max_tool_steps": 99}}, 32),
])
def test_react_step_limit_is_safe(raw: dict[str, Any], expected: int) -> None:
    assert _react_step_limit(raw) == expected


def test_commander_accepts_explicit_real_config_without_constructing_a_fake_provider(tmp_path: Path) -> None:
    config = replace(get_agent_config("commander"), model_provider="local_vllm",
                     base_url="http://127.0.0.1:1/v1", base_url_env="", api_key_env="",
                     raw={"loop": {"max_tool_steps": 6}})
    commander = Commander(orchestrator=Orchestrator(run_store=RunStore(tmp_path)), agent_config=config)
    assert commander.max_react_steps == 6
    assert commander._llm_config.provider == "local_vllm"
    # Construction is not a successful connection or completed Commander loop.


def test_plain_reply_decision_has_no_actions() -> None:
    result = _parse_decision('{"reply":"human-authored parser input","actions":[]}')
    assert result.reply == "human-authored parser input"
    assert result.actions == []


def test_decision_parser_does_not_execute_authored_actions() -> None:
    result = _parse_decision('{"actions":[null,{},{"tool":"get_run_status","args":{"run_id":"authored-input"}}]}')
    assert result.actions == [{"tool": "get_run_status", "args": {"run_id": "authored-input"}}]


def test_missing_commander_config_keeps_default_step_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "agents.yaml").write_text(yaml.safe_dump({
        "idea": {"model": {"provider": "local_vllm", "model": "unavailable-local-model",
                           "base_url": "http://127.0.0.1:1/v1"}, "loop": {"max_tool_steps": 20}},
    }))
    (configs / "models.yaml").write_text("providers: {}\n")
    # Actual temporary configuration files; model and tool execution are not replaced.
    monkeypatch.setattr(model_registry, "repo_root", lambda: tmp_path)
    model_registry.reset_cache_for_tests()
    try:
        commander = Commander(orchestrator=Orchestrator(run_store=RunStore(tmp_path / "runs")))
        assert commander.max_react_steps == DEFAULT_MAX_REACT_STEPS
        assert commander._llm_config.model == "unavailable-local-model"
    finally:
        model_registry.reset_cache_for_tests()
