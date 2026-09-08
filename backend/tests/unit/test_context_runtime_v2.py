"""Context, trace privacy and write permissions using real components."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.base import RunRequest
from app.agents.idea.agent import IdeaAgent
from app.harness.agent_loop.context import pack_context
from app.harness.agent_loop.trace import LoopTrace, digest
from app.harness.llm.provider_base import Message
from app.harness.tools.registry import ToolContext, get_registry


@pytest.mark.asyncio
async def test_context_preserves_full_upstream_and_validation_manifest() -> None:
    agent = IdeaAgent()
    upstream = "Complete approved draft:\n" + "proof boundary condition\n" * 250
    request = RunRequest(project="pimc", user_request="Improve LUT capacity",
                         upstream_artifacts={"approved": upstream},
                         extra={"required_upstream_refs": ["approved"]})
    context = await agent.build_context(request)
    pinned = agent._messages_for_context(request, context, purpose="repair-contract")
    messages, manifest = pack_context(pinned, [], "Missing exact parameter counts", "Current draft",
                                      budget=64000, observation_chars=1000)
    assert upstream in "\n".join(message.content for message in messages)
    assert messages[-1].content.endswith("Missing exact parameter counts")
    assert manifest["visible_sha256"] == digest([{"role": m.role, "content": m.content} for m in messages])
    assert manifest["omitted_history"] == []


@pytest.mark.asyncio
async def test_actual_permission_failure_keeps_raw_evidence_and_manifest(tmp_path: Path) -> None:
    registry = get_registry()
    args = {"path": str(tmp_path / "forbidden.py"), "content": "unauthorized"}
    result = await registry.dispatch("code.write_file", args,
                                    ToolContext("context-permission", "pimc", "idea",
                                                extra={"run_root": str(tmp_path)}))
    assert not result.ok
    assert result.status == "not_allowed"
    assert result.error is not None
    assert not (tmp_path / "forbidden.py").exists()
    observation = {"tool": "code.write_file", "args": args, "reason": "permission contract",
                   "ok": result.ok, "error": result.error, "output": result.output}
    trace = LoopTrace(tmp_path / "trace", "full")
    trace.emit("permission_contract", {}, visible=observation)
    rows = [json.loads(line) for line in trace.events.read_text().splitlines()]
    assert rows[0]["visible"]["error"] == result.error
    messages, manifest = pack_context([Message("system", "rules")], [observation], "", "",
                                      budget=5000, observation_chars=512)
    assert result.error in messages[-1].content
    assert manifest["omitted_history"] == []


@pytest.mark.parametrize("mode", ["metadata", "off"])
def test_trace_recording_can_exclude_visible_payloads(tmp_path: Path, mode: str) -> None:
    trace = LoopTrace(tmp_path, mode)
    content = "User-supplied trace privacy sentinel"
    trace.emit("privacy_contract", {}, visible=content)
    if mode == "off":
        assert not trace.events.exists()
    else:
        row = json.loads(trace.events.read_text())
        assert "visible" not in row
        assert row["visible_sha256"] == digest(content)
        assert content not in trace.events.read_text()


def test_idea_tool_configuration_excludes_write_capabilities() -> None:
    agent = IdeaAgent()
    registry = get_registry()
    assert agent.config.tools
    for name in agent.config.tools:
        spec = registry.spec(name)
        assert spec is not None
        assert spec.policy.mutation_level == "read"
    assert "code.write_file" not in agent.config.tools
    assert "code.apply_patch" not in agent.config.tools
    assert not registry.has("idea.research_delegate")
    request = RunRequest(project="pimc", user_request="Research", extra={"run_root": "/tmp/unused-research-catalogue"})
    from app.agents.base import ContextPack
    local_registry = agent.loop_registry(request, ContextPack("", "", ""))
    assert local_registry.has("idea.research_delegate")
    assert not registry.has("idea.research_delegate")


@pytest.mark.asyncio
async def test_missing_required_upstream_fails_before_model_request() -> None:
    agent = IdeaAgent()
    request = RunRequest(project="pimc", user_request="Respect complete upstream",
                         extra={"required_upstream_refs": ["missing"]})
    with pytest.raises(ValueError, match="required_upstream_refs"):
        await agent.build_context(request)
