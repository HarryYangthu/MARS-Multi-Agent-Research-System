from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

import pytest

import app.agents.base as base_module
import app.agents.debate.debate_runner as debate_module
import app.bridge.commander as commander_module
from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.agents.debate.debate_runner import DebateMode, run_debate
from app.bridge.commander import Commander
from app.bridge.commander_session import CommanderSession
from app.bridge.orchestrator import Orchestrator
from app.harness.llm.mock_provider import MockProvider
from app.harness.llm.model_registry import AgentConfig, get_agent_config
from app.harness.llm.provider_base import (
    LLMConfig,
    Message,
    llm_call_deadline_seconds,
)
from app.storage.run_store import RunStore


class _DeadlineAgent(BaseAgent):
    name = "deadline"
    output_schema = "proposal.v1"

    async def draft(self, request: RunRequest, context: ContextPack) -> Artifact:
        raise AssertionError("draft is not used by this test")


def _agent_config() -> AgentConfig:
    return AgentConfig(
        name="deadline",
        enabled=True,
        output_schema="proposal.v1",
        model_provider="mock",
        model_name="mock-1",
        temperature=0.0,
        max_tokens=128,
        debate_enabled=False,
        debate_rounds=1,
        debate_participants=(),
        tools=(),
        raw={},
    )


def _capture_wait_for(
    captured: list[float | None],
) -> Any:
    async def wait_for(awaitable: Awaitable[Any], timeout: float | None) -> Any:
        captured.append(timeout)
        return await awaitable

    return wait_for


def _fixed_deadline(value: float) -> Any:
    def deadline(
        _config: LLMConfig,
        *,
        minimum_seconds: float,
    ) -> float:
        assert minimum_seconds > 0.0
        return value

    return deadline


def test_llm_deadline_covers_all_attempts_backoff_and_grace() -> None:
    config = LLMConfig(
        provider="deepseek",
        model="deepseek-v4-pro",
        request_timeout_seconds=120.0,
        max_retries=3,
        retry_base_delay_seconds=1.0,
    )

    # 4 attempts * 120s + (1s + 2s + 4s) backoff + 5s grace.
    assert llm_call_deadline_seconds(config, minimum_seconds=90.0) == 492.0
    assert llm_call_deadline_seconds(config, minimum_seconds=600.0) == 600.0


@pytest.mark.asyncio
async def test_base_agent_uses_provider_attempt_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _DeadlineAgent(agent_config=_agent_config())
    config = LLMConfig(provider="mock", model="mock-1")
    monkeypatch.setattr(
        agent,
        "_select_provider",
        lambda: (MockProvider(default_schema="proposal.v1"), config),
    )
    captured: list[float | None] = []
    monkeypatch.setattr(base_module, "llm_call_deadline_seconds", _fixed_deadline(501.0))
    monkeypatch.setattr(asyncio, "wait_for", _capture_wait_for(captured))

    await agent._call_llm([Message(role="user", content="test")])

    assert captured == [501.0]


@pytest.mark.asyncio
async def test_commander_uses_provider_attempt_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "runs")
    commander = Commander(orchestrator=Orchestrator(run_store=store))
    commander._provider = MockProvider()
    commander._llm_config = LLMConfig(provider="mock", model="mock-1")
    captured: list[float | None] = []
    monkeypatch.setattr(
        commander_module,
        "llm_call_deadline_seconds",
        _fixed_deadline(502.0),
    )
    monkeypatch.setattr(
        asyncio,
        "wait_for",
        _capture_wait_for(captured),
    )

    await commander._decide_llm(
        CommanderSession(conv_id="deadline", project="pimc")
    )

    assert captured == [502.0]


@pytest.mark.asyncio
async def test_debate_runner_uses_provider_attempt_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "always")
    import app.settings as settings_module

    monkeypatch.setattr(settings_module, "_settings", None)
    captured: list[float | None] = []
    monkeypatch.setattr(
        debate_module,
        "llm_call_deadline_seconds",
        _fixed_deadline(503.0),
    )
    monkeypatch.setattr(
        asyncio,
        "wait_for",
        _capture_wait_for(captured),
    )

    await run_debate(
        agent_name="idea",
        agent_config=get_agent_config("idea"),
        request=RunRequest(project="pimc", user_request="test"),
        context=ContextPack(system="system", project="project", task="task"),
        output_schema="proposal.v1",
        mode=DebateMode.MOCK_DEBATE,
    )

    assert captured == [503.0, 503.0, 503.0]
