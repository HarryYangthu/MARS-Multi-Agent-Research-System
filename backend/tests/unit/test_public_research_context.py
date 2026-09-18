"""Real context construction for an isolated public-literature evaluation."""
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from app.agents.base import RunRequest
from app.agents.idea.agent import IdeaAgent
from app.harness.llm.model_registry import get_agent_config
from app.harness.tools.registry import get_registry
from app.settings import repo_root
from scripts.run_idea_lut_live import PUBLIC_RESEARCH_TOOLS, evaluation_request


@pytest.mark.asyncio
async def test_public_evaluation_excludes_real_project_rules_and_repository_metadata(tmp_path: Path) -> None:
    scenario = yaml.safe_load((repo_root() / "configs/evaluation/idea_2d_lut_real.yaml").read_text())
    request = evaluation_request(scenario, tmp_path)
    agent = IdeaAgent(agent_config=replace(get_agent_config("idea"), tools=PUBLIC_RESEARCH_TOOLS))
    context = await agent.build_context(request)
    assert not context.upstream
    assert context.metadata["context_sources"] == {"project_rules": False, "code_repositories": False,
                                                   "agent_resources": False, "memory": False,
                                                   "project_references": False}
    assert "No project-specific rules supplied" in context.project
    assert "project_knowledge" not in context.metadata
    assert "folder_context" not in context.metadata
    assert context.task.startswith(scenario["question"])
    original = await agent.build_context(RunRequest(project="pimc", user_request="Authored context comparison"))
    assert original.project != context.project
    assert "idea_code_repositories" in original.upstream
    assert not request.upstream_artifacts
    for name in agent.config.tools:
        spec = get_registry().spec(name)
        assert spec is not None and spec.policy.mutation_level == "read"
    assert "code.repo_reader" not in agent.config.tools
    assert "search.local_docs" not in agent.config.tools
    assert "knowledge.baseline_match" not in agent.config.tools


@pytest.mark.asyncio
async def test_public_delegated_request_does_not_reintroduce_private_resource_sources(tmp_path: Path) -> None:
    from scripts.run_idea_research_live import evaluation_request as delegated_request
    scenario = yaml.safe_load((repo_root() / "configs/evaluation/idea_research_delegated_real.yaml").read_text())
    request = delegated_request(scenario, tmp_path)
    agent = IdeaAgent()
    context = await agent.build_context(request)
    assert not context.upstream
    assert "project_knowledge" not in context.metadata
    assert "folder_context" not in context.metadata
    assert context.metadata["agent_resources"] is None
    assert context.metadata["memory"]["record_ids"] == []
    assert not (tmp_path / "input/project_knowledge.v1.json").exists()


@pytest.mark.parametrize("sources", [{"project_rules": "false"}, {"unknown": False}, []])
@pytest.mark.asyncio
async def test_invalid_context_selection_fails_before_provider_use(sources: object) -> None:
    with pytest.raises(ValueError, match="context_sources"):
        await IdeaAgent().build_context(RunRequest(project="pimc", user_request="Authored invalid configuration",
                                                   extra={"context_sources": sources}))


def test_legacy_or_project_input_cannot_silently_resume_as_public(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="legacy inputs"):
        evaluation_request({"scope": "method_proposal"}, tmp_path)
    with pytest.raises(ValueError, match="method_proposal"):
        evaluation_request({"scope": "project_proposal", "data_scope": "public_research"}, tmp_path)
