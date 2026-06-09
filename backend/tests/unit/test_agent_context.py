"""Phase F0: BaseAgent builds rich, project-grounded context (mock-safe)."""
from __future__ import annotations

import pytest

from app.agents.base import BaseAgent, RunRequest
from app.agents.idea.agent import IdeaAgent


@pytest.mark.asyncio
async def test_build_context_includes_project_rules() -> None:
    agent = IdeaAgent()
    ctx = await agent.build_context(
        RunRequest(project="moe-pimc", user_request="reduce compute, keep RES")
    )
    # 3-layer loader pulls the project's AGENTS.md into the project text.
    assert "moe-pimc" in ctx.project
    assert "baseline" in ctx.project.lower() or "AGENTS" in ctx.project
    # Idea agent's quality directives are appended to the system layer.
    assert "可证伪" in ctx.system


@pytest.mark.asyncio
async def test_build_context_sends_kb_excerpts_in_messages() -> None:
    agent = IdeaAgent()
    ctx = await agent.build_context(
        RunRequest(project="moe-pimc", user_request="hard top-2 routing")
    )
    ctx.kb_excerpts = ["prior work A on sparse routing", "method B for RES"]
    msgs = ctx.to_messages(agent_name="idea", output_schema="proposal.v1")
    blob = "\n".join(m.content for m in msgs)
    assert "knowledge base" in blob
    assert "prior work A" in blob


@pytest.mark.asyncio
async def test_build_context_falls_back_on_unknown_project() -> None:
    agent = IdeaAgent()
    # Unknown project still yields a usable context (no crash, minimal or loaded).
    ctx = await agent.build_context(
        RunRequest(project="does-not-exist", user_request="x")
    )
    assert ctx.task == "x"
    assert isinstance(ctx.system, str) and ctx.system


def test_base_agent_default_hooks_are_empty() -> None:
    # A vanilla agent keeps the no-op defaults (no KB query, no directives).
    assert BaseAgent.kb_zones == ()
    assert BaseAgent.quality_directives == ""
    assert IdeaAgent.kb_zones == ("literature", "methodology")
