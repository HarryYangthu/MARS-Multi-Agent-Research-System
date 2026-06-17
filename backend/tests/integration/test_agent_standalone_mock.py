"""5-Agent standalone-mode draft test, fully under MockProvider."""
from __future__ import annotations

import pytest

from app.agents.base import RunRequest
from app.agents.coding.agent import CodingAgent
from app.agents.execution.agent import ExecutionAgent
from app.agents.experiment.agent import ExperimentAgent
from app.agents.idea.agent import IdeaAgent
from app.agents.writing.agent import WritingAgent
from app.harness.schema.validator import validate_document


@pytest.fixture(autouse=True)
def _clear_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "QWEN_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "CUSTOM_ENDPOINT_URL",
        "CUSTOM_ENDPOINT_API_KEY",
    ):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("MARS_MOCK_MODE", "always")
    monkeypatch.setenv("LOCAL_VLLM_BASE_URL", "")
    import app.settings as settings_mod

    settings_mod._settings = None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent_cls,schema",
    [
        (IdeaAgent, "proposal.v1"),
        (ExperimentAgent, "experiment_plan.v1"),
        (CodingAgent, "code_spec.v1"),
        (ExecutionAgent, "run_log.v1"),
        (WritingAgent, "report.v1"),
    ],
)
async def test_each_agent_drafts_valid_artifact(agent_cls: type, schema: str) -> None:
    agent = agent_cls()
    request = RunRequest(project="moe-pimc", user_request="standalone test prompt")
    context = await agent.build_context(request)
    artifact = await agent.draft(request, context)
    res = validate_document(artifact.text, expected_schema=schema)
    assert res.valid, f"{agent_cls.__name__}: {res.errors}"
