"""Deterministic tests for the Idea Agent Co-Scientist workflow."""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from app.agents.base import RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.discovery import (
    CoScientistWorkflow,
    DeepDiscoveryConfig,
    DeterministicRoleBackend,
    DiscoveryProtocolError,
    IdeaMode,
    LLMRoleBackend,
    RunLocalDiscoveryStore,
    build_discovery_context,
    resolve_idea_mode,
)
from app.agents.idea.discovery.models import (
    DiscoveryContext,
    HypothesisDraft,
    ReflectionDraft,
)
from app.harness.discovery import HypothesisRecord
from app.harness.schema.validator import validate_document


def _context() -> DiscoveryContext:
    request = RunRequest(
        project="synthetic_regression",
        user_request=(
            "如何在不修改 baseline 的前提下降低模型资源并保持 validation score?"
        ),
        extra={"run_id": "run-deep-001", "node_key": "idea", "idea_mode": "deep"},
    )
    return build_discovery_context(
        request=request,
        evidence_refs=("research_evidence_1", "project_rule_1"),
        constraints=(
            "baseline protected",
            "forward interface preserved",
            "validation score stays within the frozen tolerance",
        ),
    )


def _workflow(root: Path, backend: DeterministicRoleBackend | None = None) -> CoScientistWorkflow:
    return CoScientistWorkflow(
        backend=backend or DeterministicRoleBackend(),
        config=DeepDiscoveryConfig(),
        store=RunLocalDiscoveryStore(root),
    )


@pytest.mark.asyncio
async def test_default_deep_workflow_builds_replayable_top_three(tmp_path: Path) -> None:
    root = tmp_path / "run" / "idea" / "discovery"
    workflow = _workflow(root)
    result = await workflow.run(_context())

    assert result.status == "waiting_selection"
    assert result.config.initial_hypotheses == 8
    assert result.config.evolution_rounds == 2
    assert result.config.max_pairwise_matches == 16
    assert len(result.hypotheses) == 16
    assert len(result.matches) == 16
    assert len(result.meta_reviews) == 3
    assert len(result.top_hypothesis_ids) == 3
    by_id = {item.hypothesis_id: item for item in result.hypotheses}
    assert all(not by_id[item].blocked for item in result.top_hypothesis_ids)
    assert {item.operator for item in result.hypotheses if item.parent_ids} >= {
        "strengthen",
        "combine",
        "simplify",
        "diverge",
    }

    expected_files = {
        "checkpoint.v1.json",
        "hypotheses.v1.json",
        "reflections.v1.json",
        "pairwise_matches.v1.json",
        "proximity_graphs.v1.json",
        "meta_reviews.v1.json",
        "hypothesis_pool.v1.json",
        "state.v1.json",
    }
    assert expected_files.issubset({path.name for path in root.iterdir()})
    pool = json.loads((root / "hypothesis_pool.v1.json").read_text(encoding="utf-8"))
    assert pool["status"] == "waiting_selection"
    assert pool["match_count"] == 16

    proposal = workflow.proposal_for_hitl(
        result,
        research_question=_context().research_question,
    )
    validation = validate_document(proposal.text, expected_schema="proposal.v1")
    assert validation.valid, validation.errors
    summary = proposal.metadata["discovery_summary"]
    assert isinstance(summary, dict)
    assert summary["selection_source"] == "recommended_for_hitl"
    assert summary["status"] == "waiting_selection"


class CountingBackend(DeterministicRoleBackend):
    def __init__(self) -> None:
        self.generation_calls = 0

    async def generate(
        self, context: DiscoveryContext, *, count: int
    ) -> tuple[HypothesisDraft, ...]:
        self.generation_calls += 1
        return await super().generate(context, count=count)


@pytest.mark.asyncio
async def test_completed_resume_is_idempotent_and_does_not_regenerate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "discovery"
    backend = CountingBackend()
    workflow = _workflow(root, backend)
    first = await workflow.run(_context())
    before = {
        path.name: path.read_text(encoding="utf-8")
        for path in root.iterdir()
        if path.is_file()
    }

    second = await workflow.run(_context())
    after = {
        path.name: path.read_text(encoding="utf-8")
        for path in root.iterdir()
        if path.is_file()
    }

    assert backend.generation_calls == 1
    assert second == first
    assert after == before


class OneShotReflectionFailureBackend(CountingBackend):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def reflect(
        self,
        context: DiscoveryContext,
        hypotheses: Sequence[HypothesisRecord],
    ) -> dict[str, ReflectionDraft]:
        if not self.failed:
            self.failed = True
            raise RuntimeError("simulated interruption after generation")
        return await super().reflect(context, hypotheses)


@pytest.mark.asyncio
async def test_partial_resume_reuses_completed_generation(tmp_path: Path) -> None:
    backend = OneShotReflectionFailureBackend()
    workflow = _workflow(tmp_path / "discovery", backend)

    with pytest.raises(RuntimeError, match="simulated interruption"):
        await workflow.run(_context())
    checkpoint = json.loads(
        (tmp_path / "discovery" / "checkpoint.v1.json").read_text(encoding="utf-8")
    )
    assert checkpoint["completed_stages"] == ["generation"]

    result = await workflow.run(_context())
    assert result.status == "waiting_selection"
    assert backend.generation_calls == 1


class BlockerBackend(DeterministicRoleBackend):
    async def generate(
        self, context: DiscoveryContext, *, count: int
    ) -> tuple[HypothesisDraft, ...]:
        drafts = list(await super().generate(context, count=count))
        drafts[0] = replace(
            drafts[0],
            statement="这是一个不可验证的 guaranteed 断言",
            testable_predictions=(),
            evidence_refs=(),
        )
        drafts[1] = replace(
            drafts[1],
            mechanism=drafts[2].mechanism,
            statement=drafts[2].statement,
            testable_predictions=drafts[2].testable_predictions,
            evidence_refs=drafts[2].evidence_refs,
            constraints=drafts[2].constraints,
            uncertainty=drafts[2].uncertainty,
        )
        return tuple(drafts)


@pytest.mark.asyncio
async def test_blockers_and_exact_duplicates_never_enter_top_k(tmp_path: Path) -> None:
    result = await _workflow(tmp_path / "discovery", BlockerBackend()).run(_context())
    initial = [item for item in result.hypotheses if item.round_index == 0]
    blocked = {item.hypothesis_id for item in initial if item.blocked}
    assert len(blocked) >= 2
    assert blocked.isdisjoint(result.top_hypothesis_ids)
    reflection_blockers = {
        blocker
        for item in result.reflections
        if item.hypothesis_id in blocked
        for blocker in item.blockers
    }
    assert "missing_testable_predictions" in reflection_blockers
    assert "exact_duplicate" in reflection_blockers


@pytest.mark.asyncio
async def test_human_selection_is_idempotent_and_outputs_valid_proposal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "discovery"
    workflow = _workflow(root)
    state = await workflow.run(_context())
    selected_id = state.top_hypothesis_ids[1]

    selected, proposal = workflow.select_hypothesis(
        state,
        hypothesis_id=selected_id,
        research_question=_context().research_question,
        actor="researcher@example",
        reason="机制更适合首轮最小消融",
    )
    assert selected.status == "selected"
    assert selected.selected_hypothesis_id == selected_id
    validation = validate_document(proposal.text, expected_schema="proposal.v1")
    assert validation.valid, validation.errors
    assert proposal.metadata["hypothesis"]
    selection_text = (root / "selection.v1.json").read_text(encoding="utf-8")

    replayed, replayed_proposal = workflow.select_hypothesis(
        selected,
        hypothesis_id=selected_id,
        research_question=_context().research_question,
        actor="researcher@example",
        reason="机制更适合首轮最小消融",
    )
    assert replayed == selected
    assert replayed_proposal.text == proposal.text
    assert (root / "selection.v1.json").read_text(encoding="utf-8") == selection_text


@pytest.mark.asyncio
async def test_mock_replay_is_identical_across_clean_directories(tmp_path: Path) -> None:
    first = await _workflow(tmp_path / "first").run(_context())
    second = await _workflow(tmp_path / "second").run(_context())
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


@pytest.mark.asyncio
async def test_llm_role_backend_rejects_invented_evidence_refs() -> None:
    async def complete(_role: str, _prompt: str) -> str:
        return json.dumps(
            {
                "hypotheses": [
                    {
                        "mechanism": "invented_reference",
                        "statement": "A falsifiable statement backed by an unknown source.",
                        "testable_predictions": ["metric improves under frozen protocol"],
                        "evidence_refs": ["invented/source"],
                        "constraints": ["baseline protected"],
                        "uncertainty": "requires experiment",
                    }
                ]
            }
        )

    backend = LLMRoleBackend(complete)
    with pytest.raises(DiscoveryProtocolError, match="invented evidence refs"):
        await backend.generate(_context(), count=1)


def test_auto_mode_routes_first_run_to_deep_and_revision_to_fast() -> None:
    legacy = RunRequest(project="synthetic_regression", user_request="legacy")
    first = RunRequest(
        project="synthetic_regression",
        user_request="first",
        extra={"idea_mode": "auto", "attempt": 1},
    )
    revision = RunRequest(
        project="synthetic_regression",
        user_request="revision",
        upstream_artifacts={"human_revision_request": "tighten the claim"},
        extra={"idea_mode": "auto", "attempt": 1},
    )
    explicit = RunRequest(
        project="synthetic_regression",
        user_request="deep",
        extra={"idea_mode": "deep", "attempt": 2},
    )

    assert resolve_idea_mode(legacy) is IdeaMode.FAST
    assert resolve_idea_mode(first) is IdeaMode.DEEP
    assert resolve_idea_mode(revision) is IdeaMode.FAST
    assert resolve_idea_mode(explicit) is IdeaMode.DEEP


@pytest.mark.asyncio
async def test_idea_agent_deep_mode_preserves_proposal_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_MOCK_MODE", "always")
    import app.settings as settings_mod

    settings_mod._settings = None
    agent = IdeaAgent()
    request = RunRequest(
        project="synthetic_regression",
        user_request="用深度假设发现寻找低资源且保持 validation score 的候选模型",
        extra={
            "idea_mode": "deep",
            "run_id": "agent-deep-run",
            "run_root": str(tmp_path / "run"),
            "node_key": "idea",
        },
    )
    context = await agent.build_context(request)
    artifact = await agent.draft(request, context)

    validation = validate_document(artifact.text, expected_schema="proposal.v1")
    assert validation.valid, validation.errors
    assert artifact.metadata["idea_mode"] == "deep"
    summary = artifact.metadata["discovery_summary"]
    assert isinstance(summary, dict)
    assert summary["hypothesis_count"] == 16
    assert summary["match_count"] == 16
    assert summary["selection_source"] == "recommended_for_hitl"
    assert (
        tmp_path / "run" / "idea" / "discovery" / "hypothesis_pool.v1.json"
    ).exists()
    settings_mod._settings = None
