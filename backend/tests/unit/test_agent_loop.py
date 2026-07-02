from __future__ import annotations

from pathlib import Path
import pytest

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.harness.llm.model_registry import AgentConfig
from app.harness.llm.mock_provider import build_fake_metadata
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.validator import ValidationResult, validate_document
from app.storage.run_store import RunStore


def _agent_config(loop: dict[str, object] | None = None) -> AgentConfig:
    return AgentConfig(
        name="idea",
        enabled=True,
        output_schema="proposal.v1",
        model_provider="mock",
        model_name="mock-1",
        temperature=0.0,
        max_tokens=1024,
        debate_enabled=False,
        debate_rounds=1,
        debate_participants=(),
        tools=(),
        raw={"loop": loop or {}},
    )


def _invalid_proposal() -> Artifact:
    text = "---\nschema: proposal.v1\nagent: idea\n---\n# invalid\n"
    return Artifact(
        text=text,
        schema_id="proposal.v1",
        metadata={"schema": "proposal.v1", "agent": "idea"},
        body="# invalid\n",
    )


def _valid_proposal(seed: str = "repair") -> Artifact:
    metadata = build_fake_metadata("proposal.v1", seed=seed)
    body = "# repaired\n\n修复后的 proposal。"
    return Artifact(
        text=fm_dumps(metadata, body),
        schema_id="proposal.v1",
        metadata=metadata,
        body=body,
    )


class _RepairingAgent(BaseAgent):
    name = "idea"
    output_schema = "proposal.v1"

    def __init__(self, *, loop: dict[str, object] | None = None) -> None:
        super().__init__(agent_config=_agent_config(loop))
        self.draft_calls = 0
        self.repair_calls = 0
        self.last_validation_errors = ""

    async def draft(self, request: RunRequest, context: ContextPack) -> Artifact:
        self.draft_calls += 1
        return _invalid_proposal()

    async def repair_after_validation_failure(
        self,
        *,
        request: RunRequest,
        context: ContextPack,
        artifact: Artifact,
        validation: ValidationResult,
        attempt: int,
    ) -> Artifact:
        self.repair_calls += 1
        repair_context = self._validation_repair_context(
            context=context,
            artifact=artifact,
            validation=validation,
            attempt=attempt,
        )
        self.last_validation_errors = repair_context.upstream[
            "idea_schema_errors_attempt_1"
        ]
        return _valid_proposal()


class _RunnerAgent(_RepairingAgent):
    async def run_loop(self, request: RunRequest, context: ContextPack) -> Artifact:
        self.repair_calls += 1
        return _valid_proposal(seed="runner")


@pytest.mark.asyncio
async def test_agent_loop_repairs_schema_invalid_draft() -> None:
    agent = _RepairingAgent(loop={"max_validation_repairs": 1, "max_tool_steps": 5})
    request = RunRequest(project="pimc", user_request="repair test")
    context = await agent.build_context(request)

    artifact = await agent.run_loop(request, context)

    result = validate_document(artifact.text, expected_schema="proposal.v1")
    assert result.valid, result.errors
    assert agent.draft_calls == 1
    assert agent.repair_calls == 1
    assert agent.loop_policy.max_tool_steps == 5
    assert "project" in agent.last_validation_errors


@pytest.mark.asyncio
async def test_agent_loop_preserves_invalid_artifact_when_repair_disabled() -> None:
    agent = _RepairingAgent(loop={"max_validation_repairs": 0})
    request = RunRequest(project="pimc", user_request="repair disabled")
    context = await agent.build_context(request)

    artifact = await agent.run_loop(request, context)

    result = validate_document(artifact.text, expected_schema="proposal.v1")
    assert not result.valid
    assert agent.draft_calls == 1
    assert agent.repair_calls == 0


@pytest.mark.asyncio
async def test_agent_runner_uses_agent_loop(tmp_path: Path) -> None:
    from app.bridge.agent_registry import get_registry, reset_registry_for_tests
    from app.bridge.agent_runner import run_agent_node
    from app.harness.kb.stores import reset_for_tests as reset_kb_stores

    reset_registry_for_tests()
    reset_kb_stores(tmp_path / "knowledge")
    agent = _RunnerAgent(loop={"max_validation_repairs": 1})
    get_registry().register("idea", agent)
    run = RunStore(tmp_path / "runs").create(
        task="agent-loop",
        project="pimc",
        entrypoint="idea",
        user_request="runner should use run_loop",
    )

    try:
        await run_agent_node(run, "idea")
    finally:
        reset_registry_for_tests()
        reset_kb_stores()

    artifact_path = run.subdir("idea") / "idea_proposal.v1.md"
    result = validate_document(
        artifact_path.read_text(encoding="utf-8"),
        expected_schema="proposal.v1",
    )
    assert result.valid, result.errors
    assert agent.draft_calls == 0
    assert agent.repair_calls == 1
