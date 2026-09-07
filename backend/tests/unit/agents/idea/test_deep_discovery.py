"""Pure discovery contracts, real files and actual API refusal; no model substitutes.

The manually authored records below do not represent LLM generation or review.
Full Co-Scientist success requires a separate real-model evaluation before enablement.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path
import pytest
from openai import APIConnectionError
from pydantic import ValidationError
from app.agents.base import RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.discovery import (
    CoScientistWorkflow, DeepDiscoveryConfig, DiscoveryProtocolError, IdeaMode,
    LLMRoleBackend, RunLocalDiscoveryStore, build_discovery_context, resolve_idea_mode,
)
from app.agents.idea.discovery.backend import _extract_json, _require_known_evidence
from app.agents.idea.discovery.models import DeepDiscoveryState, HypothesisSelection
from app.agents.idea.discovery.proximity import build_proximity_graph
from app.agents.idea.discovery.ranking import _schedule_pairs, select_top_hypotheses
from app.agents.idea.discovery.reflection import _hard_blockers
from app.agents.idea.discovery.storage import DiscoveryInputMismatchError
from app.harness.discovery import HypothesisRecord
from app.harness.llm.openai_provider import LocalVllmProvider
from app.harness.llm.provider_base import LLMConfig, Message


def authored_hypothesis(identifier: str, statement: str, **changes: object) -> HypothesisRecord:
    return HypothesisRecord.model_validate({
        "hypothesis_id": identifier, "run_id": "authored-record-contract", "round_index": 0,
        "mechanism": "manually specified mechanism", "statement": statement,
        "testable_predictions": ["Compare an explicitly defined held-out error"],
        "evidence_refs": ["human-notes.md"], "constraints": ["No empirical result claimed"],
        "uncertainty": "Requires real research and experimentation", "operator": "generate", **changes,
    })


def test_deep_budget_configuration_remains_bounded() -> None:
    default = DeepDiscoveryConfig()
    assert (default.initial_hypotheses, default.evolution_rounds, default.max_pairwise_matches, default.top_k) == (8, 2, 16, 3)
    fast = DeepDiscoveryConfig.from_request_extra({"idea_budget_profile": "fast"})
    assert fast.initial_hypotheses == 4 and fast.max_pairwise_matches == 6
    with pytest.raises(ValidationError):
        DeepDiscoveryConfig(initial_hypotheses=33)
    with pytest.raises(ValidationError):
        DeepDiscoveryConfig(initial_hypotheses=3, evolution_rounds=0, top_k=4)


def test_authored_blockers_and_duplicates_cannot_enter_top_k() -> None:
    first = authored_hypothesis("a", "Learn ordered knot spacing under a fixed parameter budget")
    duplicate = first.model_copy(update={"hypothesis_id": "b"})
    blocked = authored_hypothesis("c", "A guaranteed claim without an observable prediction",
                                  testable_predictions=[], evidence_refs=[], blocked=True, elo=9000)
    assert {"missing_testable_predictions", "missing_evidence_refs", "not_falsifiable"} <= set(_hard_blockers(blocked))
    other = authored_hypothesis("d", "A separable matrix factorization limits stored coefficients")
    pool, graph, duplicates = build_proximity_graph((first, duplicate, blocked, other), round_index=0, threshold=0.9)
    assert duplicates == ("b",) and graph.edges[0].exact_duplicate
    assert set(select_top_hypotheses(pool, top_k=3)) == {"a", "d"}
    pairs = _schedule_pairs(pool, existing_matches=(), round_index=0, limit=16)
    assert len(pairs) == 1 and {h.hypothesis_id for h in pairs[0]} == {"a", "d"}


def test_authored_snapshots_are_idempotent_and_reject_other_inputs(tmp_path: Path) -> None:
    state = DeepDiscoveryState(run_id="authored-record-contract", project="pimc", input_hash="authored-input-digest",
        config=DeepDiscoveryConfig(), backend_mode="human_authored",
        hypotheses=(authored_hypothesis("a", "An explicit human-authored hypothesis for storage"),))
    store = RunLocalDiscoveryStore(tmp_path / "first")
    store.save_state(state)
    before = {p.name: p.read_bytes() for p in (tmp_path / "first").iterdir()}
    store.save_state(state)
    assert before == {p.name: p.read_bytes() for p in (tmp_path / "first").iterdir()}
    assert store.load_state(input_hash=state.input_hash) == state
    with pytest.raises(DiscoveryInputMismatchError):
        store.load_state(input_hash="different-input")
    RunLocalDiscoveryStore(tmp_path / "second").save_state(state)
    assert before == {p.name: p.read_bytes() for p in (tmp_path / "second").iterdir()}
    assert json.loads(before["checkpoint.v1.json"])["completed_stages"] == []


def test_human_selection_is_idempotent_and_rejects_conflicts(tmp_path: Path) -> None:
    store = RunLocalDiscoveryStore(tmp_path)
    selection = HypothesisSelection(selection_id="human-selection", run_id="authored-record-contract", hypothesis_id="a",
        actor="researcher", source="human", proposal_metadata={}, proposal_body="Human notes")
    store.save_selection(selection)
    before = (tmp_path / "selection.v1.json").read_bytes()
    store.save_selection(selection)
    assert (tmp_path / "selection.v1.json").read_bytes() == before
    with pytest.raises(RuntimeError, match="different hypothesis"):
        store.save_selection(selection.model_copy(update={"hypothesis_id": "b"}))


def test_role_parser_rejects_invented_evidence_without_a_model_substitute() -> None:
    assert _extract_json('{"hypotheses": []}') == {"hypotheses": []}
    with pytest.raises(DiscoveryProtocolError, match="valid JSON"):
        _extract_json('{"hypotheses": ')
    with pytest.raises(DiscoveryProtocolError, match="invented evidence refs"):
        _require_known_evidence(("unknown/source",), allowed=("human-notes.md",), role="generation")


@pytest.mark.asyncio
async def test_actual_failed_generation_never_checkpoints_a_completed_stage(tmp_path: Path) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        provider = LocalVllmProvider(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}/v1")
        config = LLMConfig(provider="local_vllm", model="unavailable-local-model", request_timeout_seconds=0.3, max_retries=0)
        async def complete(role: str, prompt: str) -> str:
            return (await provider.complete([Message("system", role), Message("user", prompt)], config)).text
        workflow = CoScientistWorkflow(backend=LLMRoleBackend(complete), config=DeepDiscoveryConfig(),
                                       store=RunLocalDiscoveryStore(tmp_path))
        context = build_discovery_context(request=RunRequest(project="pimc", user_request="Actual transport failure",
            extra={"run_id": "actual-refusal"}), evidence_refs=(), constraints=())
        try:
            for _ in range(2):
                with pytest.raises(APIConnectionError):
                    await workflow.run(context)
                state = json.loads((tmp_path / "checkpoint.v1.json").read_text())
                assert state["completed_stages"] == [] and state["hypothesis_count"] == 0
        finally:
            await provider.close()


def test_legacy_mode_router_is_a_configuration_contract_only() -> None:
    assert resolve_idea_mode(RunRequest(project="pimc", user_request="default")) is IdeaMode.FAST
    assert resolve_idea_mode(RunRequest(project="pimc", user_request="first", extra={"idea_mode": "auto"})) is IdeaMode.DEEP
    revision = RunRequest(project="pimc", user_request="revise", extra={"idea_mode": "auto"},
                          upstream_artifacts={"human_revision_request": "tighten the claim"})
    assert resolve_idea_mode(revision) is IdeaMode.FAST


@pytest.mark.asyncio
async def test_unwired_deep_mode_fails_before_research_or_proposal(tmp_path: Path) -> None:
    agent = IdeaAgent()
    request = RunRequest(project="pimc", user_request="Deep-mode contract",
                         extra={"idea_mode": "deep", "run_root": str(tmp_path)})
    with pytest.raises(ValueError, match="not wired"):
        await agent.draft(request, await agent.build_context(request))
    assert not list(tmp_path.rglob("*.md")) and not list(tmp_path.rglob("events.jsonl"))
