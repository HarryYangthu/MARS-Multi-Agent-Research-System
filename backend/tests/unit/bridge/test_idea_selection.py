"""Actual human-selection artifacts and failures; no replacement Idea Agent."""
from __future__ import annotations

import json
from pathlib import Path
import pytest
from app.bridge.agent_registry import AgentRegistry
from app.bridge.discovery_service import IdeaSelectionRequest
from app.bridge.idea_selection import IdeaSelectionCoordinator, IdeaSelectionError, _agent_request
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunStore


@pytest.mark.asyncio
async def test_human_selection_materializes_once_and_preserves_request_options(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    run = store.create(task="human-selection", project="pimc", entrypoint="idea", user_request="Select human-authored notes")
    selection = IdeaSelectionRequest(run_id=run.run_id, hypothesis_id="human-hypothesis", idempotency_key="human-request",
        actor="researcher", reason="explicit comparison", selection_request_ref="human-request.json")
    metadata = {"schema": "proposal.v1", "project": run.project, "agent": "idea",
        "research_question": "Which manually described method should be studied?",
        "hypothesis": "A human-authored hypothesis requiring real research and validation",
        "novelty": "No novelty claim; this is a document persistence test"}
    artifacts = ArtifactStore(run)
    artifacts.write(text=fm_dumps(metadata, "Human-authored initial notes"), expected_schema="proposal.v1")
    path = run.root / "idea/discovery/selection.v1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"hypothesis_id": selection.hypothesis_id, "source": "human",
        "proposal_metadata": {**metadata, "discovery_summary": {"selected_hypothesis_id": selection.hypothesis_id,
                                                              "selection_source": "human"}},
        "proposal_body": "Explicit human selection; no model generation performed"}))
    (run.root / "input/run_request_options.v1.json").write_text(json.dumps({"schema_id": "run_request_options.v1",
        "extra": {"idea_budget_profile": "fast", "project_inputs": {"mode": "real"}}}))
    coordinator = IdeaSelectionCoordinator(run_store=store, registry=AgentRegistry())
    first = await coordinator.apply(selection)
    assert first == await coordinator.apply(selection) == "idea/idea_proposal.v2.md"
    assert len(artifacts.list_versions(agent_dir="idea", stem="idea_proposal")) == 2
    request = _agent_request(run, selection)
    assert request.extra["project_inputs"] == {"mode": "real"}
    assert request.extra["idea_budget_profile"] == "fast"
    assert request.extra["idea_selected_hypothesis_id"] == selection.hypothesis_id


@pytest.mark.asyncio
async def test_missing_agent_and_selection_checkpoint_fails_explicitly(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = store.create(task="missing-agent", project="pimc", entrypoint="idea", user_request="Selection contract")
    selection = IdeaSelectionRequest(run_id=run.run_id, hypothesis_id="human-hypothesis", idempotency_key="request",
        actor="researcher", reason="test", selection_request_ref="request.json")
    with pytest.raises(IdeaSelectionError, match="not registered"):
        await IdeaSelectionCoordinator(run_store=store, registry=AgentRegistry()).apply(selection)
    assert not list((run.root / "idea").glob("*.md"))
