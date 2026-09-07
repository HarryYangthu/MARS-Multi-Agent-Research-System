"""Real BaseAgent contracts and bridge failure persistence, without agent doubles."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.agents.base import Artifact, RunRequest
from app.agents.idea.agent import IdeaAgent
from app.bridge.agent_registry import get_registry, reset_registry_for_tests
from app.bridge.agent_runner import _write_agent_failure_diagnostic, run_agent_node
from app.harness.agent_loop.context import pack_context
from app.harness.llm.model_registry import get_agent_config
from app.harness.llm.provider_base import LLMCompletionError
from app.storage.run_store import RunStore

INVALID_DOCUMENT = "---\nschema: proposal.v1\nagent: idea\n---\n# Missing required fields\n"


@pytest.mark.asyncio
async def test_schema_repair_feedback_preserves_actual_candidate_and_errors() -> None:
    agent = IdeaAgent()
    request = RunRequest(project="pimc", user_request="Validate a manually supplied draft")
    errors = await agent.validate_candidate(request, INVALID_DOCUMENT, [])
    assert errors
    assert any("project" in value for value in errors)
    context = await agent.build_context(request)
    pinned = agent._messages_for_context(request, context, purpose="schema-repair-contract")
    feedback = json.dumps({"validation_errors": errors})
    messages, manifest = pack_context(pinned, [], feedback, INVALID_DOCUMENT,
                                      budget=64000, observation_chars=1000)
    assert INVALID_DOCUMENT in json.loads(messages[-2].content)["candidate"]
    assert feedback in messages[-1].content
    assert manifest["omitted_history"] == []
    # The host reports validation errors; it does not author a repaired proposal.
    assert not (await agent.validate_output(Artifact(INVALID_DOCUMENT, "proposal.v1", {}, ""))).valid


@pytest.mark.asyncio
async def test_disabling_repair_does_not_accept_an_invalid_document() -> None:
    cfg = get_agent_config("idea")
    agent = IdeaAgent(agent_config=replace(cfg, raw={**cfg.raw, "loop": {"max_validation_repairs": 0}}))
    assert agent.loop_policy.max_validation_repairs == 0
    artifact = Artifact(INVALID_DOCUMENT, "proposal.v1", {}, "")
    assert not (await agent.validate_output(artifact)).valid


@pytest.mark.asyncio
async def test_bridge_invokes_real_agent_and_persists_configuration_failure(tmp_path: Path) -> None:
    reset_registry_for_tests()
    cfg = replace(get_agent_config("idea"), model_provider="unconfigured")
    get_registry().register("idea", IdeaAgent(agent_config=cfg))
    run = RunStore(tmp_path / "runs").create(task="real-configuration-failure", project="pimc",
                                             entrypoint="idea", user_request="Propose a LUT method")
    try:
        with pytest.raises(RuntimeError, match="not configured"):
            await run_agent_node(run, "idea")
    finally:
        reset_registry_for_tests()
    assert not (run.subdir("idea") / "idea_proposal.v1.md").exists()
    events = [json.loads(line) for line in (run.subdir("events") / "agent_events.jsonl").read_text().splitlines()]
    failure = events[-1]
    assert failure["event"] == "agent.node_failed"
    assert failure["phase"] == "draft"
    assert failure["diagnostic"]["exception_type"] == "RuntimeError"


def test_diagnostic_serializer_persists_only_content_free_fields(tmp_path: Path) -> None:
    run = RunStore(tmp_path / "runs").create(task="diagnostic-serialization", project="pimc",
                                             entrypoint="idea", user_request="serializer contract")
    error = LLMCompletionError(code="empty_final_content", provider="zhipu", model="glm-5.3",
                               finish_reason="stop", empty_final=True)
    # Test the actual exception serializer directly, not a fabricated provider failure.
    _write_agent_failure_diagnostic(run=run, node_key="idea", agent="idea", phase="draft", exc=error)
    event = json.loads((run.subdir("events") / "agent_events.jsonl").read_text().splitlines()[-1])
    assert event["diagnostic"] == {
        "code": "empty_final_content", "exception_type": "LLMCompletionError",
        "details": {"code": "empty_final_content", "provider": "zhipu", "model": "glm-5.3",
                    "finish_reason": "stop", "empty_final": True},
    }
