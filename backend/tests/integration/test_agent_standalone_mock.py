"""Five-agent real configuration checks. No fake successful drafts."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.agents.base import BaseAgent, RunRequest
from app.agents.coding.agent import CodingAgent
from app.agents.execution.agent import ExecutionAgent
from app.agents.experiment.agent import ExperimentAgent
from app.agents.idea.agent import IdeaAgent
from app.agents.writing.agent import WritingAgent
from app.harness.agent_loop.executor import NativeAgentLoop
from app.harness.llm.model_registry import get_agent_config

AGENTS = (IdeaAgent, ExperimentAgent, CodingAgent, ExecutionAgent, WritingAgent)


@pytest.mark.parametrize("agent_cls", AGENTS)
@pytest.mark.parametrize("mode", ["react", "reflection"])
def test_each_agent_uses_shared_native_loop(agent_cls: type[BaseAgent], mode: str) -> None:
    config = get_agent_config(agent_cls.name)
    config = replace(config, raw={**config.raw, "loop": {"mode": mode}})
    agent = agent_cls(agent_config=config)
    assert isinstance(agent._executor, NativeAgentLoop)
    assert agent.loop_policy.mode == mode
    assert agent.output_schema == config.output_schema


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_cls", AGENTS)
async def test_missing_provider_fails_without_creating_an_answer(
    agent_cls: type[BaseAgent], tmp_path: Path,
) -> None:
    config = get_agent_config(agent_cls.name)
    config = replace(config, model_provider="not-configured", api_key_env="", base_url="", debate_enabled=False)
    agent = agent_cls(agent_config=config)
    request = RunRequest(
        project="pimc", user_request="Check explicit missing-provider failure.",
        extra={"run_id": "missing-provider", "run_root": str(tmp_path)},
    )
    context = await agent.build_context(request)
    with pytest.raises(RuntimeError, match="not configured"):
        await agent._draft_via_llm(request, context)
    assert list(tmp_path.rglob("*.md")) == []
