from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.bridge.agent_registry import AgentRegistry
from app.bridge.discovery_service import IdeaSelectionRequest
from app.bridge.idea_selection import IdeaSelectionCoordinator
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.validator import validate_document
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunStore


class _Validation:
    valid = True

    @staticmethod
    def first_error() -> str | None:
        return None


class _Artifact:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeIdeaAgent:
    name = "idea"
    output_schema = "proposal.v1"

    def __init__(self) -> None:
        self.calls = 0

    async def build_context(self, request: Any) -> object:
        return object()

    async def draft(self, request: Any, context: object) -> _Artifact:
        del context
        self.calls += 1
        hypothesis_id = str(request.extra["idea_selected_hypothesis_id"])
        metadata = _proposal_metadata(request.project, hypothesis_id)
        selection = {
            "schema_id": "hypothesis_selection.v1",
            "selection_id": "selection-1",
            "run_id": request.extra["run_id"],
            "hypothesis_id": hypothesis_id,
            "actor": request.extra["idea_selection_actor"],
            "reason": request.extra["idea_selection_reason"],
            "source": "human",
            "proposal_metadata": metadata,
            "proposal_body": "# Selected proposal\n",
        }
        root = Path(str(request.extra["run_root"])) / "idea" / "discovery"
        root.mkdir(parents=True, exist_ok=True)
        (root / "selection.v1.json").write_text(json.dumps(selection), encoding="utf-8")
        return _Artifact(fm_dumps(metadata, "# Selected proposal\n"))

    async def validate_output(self, artifact: _Artifact) -> _Validation:
        assert validate_document(artifact.text, expected_schema="proposal.v1").valid
        return _Validation()

    async def revise(self, artifact: _Artifact, feedback: object) -> _Artifact:
        del feedback
        return artifact


def _proposal_metadata(project: str, hypothesis_id: str) -> dict[str, object]:
    return {
        "schema": "proposal.v1",
        "project": project,
        "agent": "idea",
        "research_question": "Which bounded synthetic configuration should be tested?",
        "hypothesis": "A bounded configuration improves the validation objective.",
        "novelty": "The configuration is selected from an auditable hypothesis tournament.",
        "discovery_summary": {
            "selected_hypothesis_id": hypothesis_id,
            "selection_source": "human",
        },
    }


@pytest.mark.asyncio
async def test_selection_materializes_one_idempotent_proposal(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    run = store.create(
        task="idea-selection",
        project="synthetic_regression",
        entrypoint="idea",
        user_request="Select one bounded hypothesis.",
    )
    agent = _FakeIdeaAgent()
    registry = AgentRegistry()
    registry.register("idea", agent)
    coordinator = IdeaSelectionCoordinator(run_store=store, registry=registry)
    selection = IdeaSelectionRequest(
        run_id=run.run_id,
        hypothesis_id="hypothesis-1",
        idempotency_key="selection-request-1",
        actor="researcher",
        reason="best falsifiable candidate",
        selection_request_ref="idea/discovery/selection_requests/request.json",
    )

    first = await coordinator.apply(selection)
    second = await coordinator.apply(selection)

    assert first == second
    assert agent.calls == 1
    assert first == "idea/idea_proposal.v1.md"
    assert (run.root / "idea" / "discovery" / "selection.v1.json").is_file()
    assert len(
        [
            item
            for item in ArtifactStore(run).list_versions(
                agent_dir="idea", stem="idea_proposal"
            )
            if item.version != "approved"
        ]
    ) == 1
