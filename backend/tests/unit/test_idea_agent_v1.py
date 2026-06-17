"""V1 Idea Agent research/context behavior."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.base import ContextPack, RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.research import (
    load_idea_self_context,
    prepare_research_pack,
    validate_idea_quality,
)
from app.harness.llm.mock_provider import build_fake_metadata
from app.harness.schema.validator import validate_document


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_loads_idea_self_context_assets() -> None:
    entries = load_idea_self_context()
    paths = {entry.path for entry in entries}
    assert any(path.startswith("docs/") for path in paths)
    assert any(path.startswith("prompts/") for path in paths)
    assert any(path.startswith("examples/") for path in paths)
    assert any(path.startswith("evals/") for path in paths)
    assert "agent.py" in paths
    assert "research.py" in paths
    assert any("先调研" in entry.text for entry in entries)


@pytest.mark.asyncio
async def test_idea_context_includes_self_context_and_project_rules(
    tmp_path: Path,
) -> None:
    agent = IdeaAgent()
    request = RunRequest(
        project="moe-pimc",
        user_request="如何在 8L 配置下降低 ATK-MoE 资源并保持 RES?",
        extra={"idea_research_dir": str(tmp_path / "research")},
    )
    context = await agent.build_context(request)
    assert "Project AGENTS.md" in context.project
    assert "idea_self_context" in context.upstream
    assert "idea_research_sites" in context.upstream
    assert "arXiv" in context.upstream["idea_research_sites"]
    assert "Idea Agent 自上下文" in context.upstream["idea_self_context"]


@pytest.mark.asyncio
async def test_idea_draft_writes_research_pack_before_valid_proposal(
    tmp_path: Path,
) -> None:
    research_dir = tmp_path / "idea" / "research"
    agent = IdeaAgent()
    request = RunRequest(
        project="moe-pimc",
        user_request="如何在 8L 配置下降低 ATK-MoE 资源并保持 RES?",
        extra={"idea_research_dir": str(research_dir)},
    )
    context = await agent.build_context(request)
    artifact = await agent.draft(request, context)

    assert (research_dir / "research_plan.v1.md").exists()
    assert (research_dir / "research_notes.v1.md").exists()
    assert (research_dir / "research_summary.v1.md").exists()
    evidence_path = research_dir / "evidence_index.v1.json"
    assert evidence_path.exists()
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["items"]
    assert evidence["network_research"] == "disabled"
    assert any(item["kind"] == "self_context" for item in evidence["items"])

    result = validate_document(artifact.text, expected_schema="proposal.v1")
    assert result.valid, result.errors
    assert "research_artifacts" in artifact.metadata
    assert artifact.metadata["research_artifacts"]["research_dir"] == str(research_dir)


def test_research_pack_records_network_research_toggle(tmp_path: Path) -> None:
    request = RunRequest(
        project="moe-pimc",
        user_request="联网调研是否可选?",
        extra={
            "idea_research_dir": str(tmp_path / "research"),
            "enable_network_research": "true",
        },
    )
    context = ContextPack(system="", project="", task=request.user_request)
    pack = prepare_research_pack(
        request=request,
        context=context,
        research_config={"enable_network": False},
    )
    assert pack.evidence_index["network_research"] == "enabled_configured_no_fetcher"
    assert "enabled by config" in pack.plan_md


def test_idea_quality_warns_for_missing_prediction() -> None:
    metadata = build_fake_metadata("proposal.v1", seed="abc123")
    metadata.pop("testable_predictions")
    warnings = validate_idea_quality(metadata)
    assert "missing_testable_predictions" in warnings


def test_idea_quality_warns_for_missing_evidence_ref() -> None:
    metadata = build_fake_metadata("proposal.v1", seed="abc123")
    metadata.pop("evidence_refs")
    warnings = validate_idea_quality(metadata)
    assert "missing_evidence_refs" in warnings


def test_idea_quality_passes_for_v1_mock_proposal() -> None:
    metadata = build_fake_metadata("proposal.v1", seed="abc123")
    assert validate_idea_quality(metadata) == []
