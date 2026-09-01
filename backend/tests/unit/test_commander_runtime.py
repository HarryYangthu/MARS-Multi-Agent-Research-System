from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import app.bridge.commander as commander_module
from app.bridge.commander import (
    DEFAULT_MAX_REACT_STEPS,
    Commander,
    Decision,
    _react_step_limit,
)
from app.bridge.commander_session import CommanderSession
from app.bridge.orchestrator import Orchestrator
from app.harness.llm.mock_provider import MockProvider
from app.harness.llm.model_registry import AgentConfig, get_agent_config
from app.harness.llm.provider_base import LLMConfig
from app.storage.run_store import RunStore


def _configure_commander(
    monkeypatch: pytest.MonkeyPatch,
    *,
    max_tool_steps: int,
) -> None:
    base = get_agent_config("commander")
    raw = dict(base.raw)
    raw["loop"] = {"max_tool_steps": max_tool_steps}
    config = replace(base, raw=raw)
    monkeypatch.setattr(
        commander_module,
        "get_agent_config",
        lambda _name: config,
    )
    monkeypatch.setattr(
        commander_module,
        "select_provider",
        lambda _config: (
            MockProvider(),
            LLMConfig(provider="mock", model="mock-1"),
        ),
    )


def _commander(tmp_path: Path) -> Commander:
    store = RunStore(tmp_path / "runs")
    return Commander(orchestrator=Orchestrator(run_store=store))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({}, DEFAULT_MAX_REACT_STEPS),
        ({"loop": "invalid"}, DEFAULT_MAX_REACT_STEPS),
        ({"loop": {"max_tool_steps": True}}, DEFAULT_MAX_REACT_STEPS),
        ({"loop": {"max_tool_steps": "invalid"}}, DEFAULT_MAX_REACT_STEPS),
        ({"loop": {"max_tool_steps": 0}}, 1),
        ({"loop": {"max_tool_steps": "8"}}, 8),
        ({"loop": {"max_tool_steps": 99}}, 32),
    ],
)
def test_react_step_limit_is_safe(
    raw: dict[str, Any],
    expected: int,
) -> None:
    assert _react_step_limit(raw) == expected


@pytest.mark.asyncio
async def test_commander_uses_configured_react_step_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_commander(monkeypatch, max_tool_steps=6)
    commander = _commander(tmp_path)
    session = CommanderSession(conv_id="configured-steps", project="pimc")
    decision_calls = 0
    tool_calls = 0

    async def decide(_session: CommanderSession) -> Decision:
        nonlocal decision_calls
        decision_calls += 1
        return Decision(actions=[{"tool": "test.tool", "args": {}}])

    async def execute_tool(
        _tool: str,
        _args: dict[str, Any],
        _ctx: Any,
    ) -> dict[str, Any]:
        nonlocal tool_calls
        tool_calls += 1
        return {"ok": True}

    monkeypatch.setattr(commander, "_decide", decide)
    monkeypatch.setattr(commander_module, "execute_tool", execute_tool)

    emitted = await commander.handle_user_message(session, "continue")

    assert commander.max_react_steps == 6
    assert decision_calls == 6
    assert tool_calls == 6
    assert len(emitted) == 6


@pytest.mark.asyncio
async def test_commander_still_stops_immediately_without_actions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_commander(monkeypatch, max_tool_steps=12)
    commander = _commander(tmp_path)
    session = CommanderSession(conv_id="early-stop", project="pimc")
    decision_calls = 0

    async def decide(_session: CommanderSession) -> Decision:
        nonlocal decision_calls
        decision_calls += 1
        return Decision(reply="done")

    monkeypatch.setattr(commander, "_decide", decide)

    emitted = await commander.handle_user_message(session, "status")

    assert decision_calls == 1
    assert [message.content for message in emitted] == ["done"]


def test_missing_commander_config_keeps_default_step_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    idea_config = replace(
        get_agent_config("idea"),
        raw={"loop": {"max_tool_steps": 20}},
    )

    def config(name: str) -> AgentConfig:
        if name == "commander":
            raise KeyError(name)
        return idea_config

    monkeypatch.setattr(commander_module, "get_agent_config", config)
    monkeypatch.setattr(
        commander_module,
        "select_provider",
        lambda _config: (
            MockProvider(),
            LLMConfig(provider="mock", model="mock-1"),
        ),
    )

    commander = _commander(tmp_path)

    assert commander.max_react_steps == DEFAULT_MAX_REACT_STEPS
