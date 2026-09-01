from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import dependencies as deps
from app.bridge.agent_registry import get_registry, reset_registry_for_tests
from app.bridge.extension_runtime import reset_extension_runtime
from app.bridge.orchestrator import Orchestrator
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.schema.frontmatter_parser import parse as parse_frontmatter
from app.harness.schema.validator import validate_document
from app.main import create_app
from app.settings import reset_settings_cache
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunStore


def test_main_app_runs_deep_idea_and_materializes_idempotent_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARS_MOCK_MODE", "always")
    monkeypatch.setenv("MARS_DISTRIBUTION", "v31-wireless")
    monkeypatch.setenv("MARS_PROJECT_PACK_PATHS", "")
    reset_settings_cache()
    reset_extension_runtime()
    reset_registry_for_tests()
    store = RunStore(tmp_path / "runs")
    bus = InProcessEventBus()
    deps._run_store = store
    deps._bus = bus
    deps._orchestrator = Orchestrator(
        run_store=store,
        bus=bus,
        registry=get_registry(),
    )
    try:
        with TestClient(create_app()) as client:
            created = client.post(
                "/api/runs",
                json={
                    "task": "idea-co-scientist-e2e",
                    "project": "synthetic_regression",
                    "entrypoint": "idea",
                    "standalone": True,
                    "user_request": (
                        "Find one bounded deterministic regression hypothesis."
                    ),
                    "auto_approve": True,
                    "idea_mode": "auto",
                    "idea_budget_profile": "balanced",
                    "project_inputs": {"mode": "mock"},
                },
            )
            assert created.status_code == 200, created.text
            run_id = str(created.json()["run_id"])
            started = client.post(f"/api/runs/{run_id}/start")
            assert started.status_code == 202, started.text

            overview: dict[str, object] = {}
            for _ in range(100):
                response = client.get(f"/api/runs/{run_id}/idea-discovery")
                assert response.status_code == 200, response.text
                overview = response.json()
                if overview.get("status") == "waiting_selection":
                    break
                time.sleep(0.05)
            assert overview.get("status") == "waiting_selection"
            assert overview["idea_mode"] == "auto"
            assert overview["backend_mode"] == "deterministic_mock"
            hypotheses = overview["hypotheses"]
            assert isinstance(hypotheses, list)
            assert len([item for item in hypotheses if item["round_index"] == 0]) == 8
            assert len(hypotheses) == 16
            reflections = overview["reflections"]
            matches = overview["matches"]
            proximity_graphs = overview["proximity_graphs"]
            meta_reviews = overview["meta_reviews"]
            assert isinstance(reflections, list)
            assert isinstance(matches, list)
            assert isinstance(proximity_graphs, list)
            assert isinstance(meta_reviews, list)
            assert len(reflections) == 16
            assert 0 < len(matches) <= 16
            assert len(proximity_graphs) == 3
            assert len(meta_reviews) == 3
            finalists = overview["finalist_ids"]
            assert isinstance(finalists, list)
            assert len(finalists) == 3

            add_payload = {
                "actor": "e2e-researcher",
                "reason": "add a bounded orthogonal mechanism",
                "statement": "A human-proposed bounded feature improves validation.",
            }
            added = client.post(
                f"/api/runs/{run_id}/idea-discovery/hypotheses",
                json=add_payload,
            )
            repeated_add = client.post(
                f"/api/runs/{run_id}/idea-discovery/hypotheses",
                json=add_payload,
            )
            assert added.status_code == 200, added.text
            assert repeated_add.json() == added.json()
            human_id = str(added.json()["hypothesis_id"])
            edited = client.patch(
                f"/api/runs/{run_id}/idea-discovery/hypotheses/{human_id}",
                json={
                    "actor": "e2e-researcher",
                    "reason": "make the intervention directly falsifiable",
                    "statement": (
                        "A human-edited bounded feature lowers validation loss."
                    ),
                },
            )
            rejected = client.post(
                f"/api/runs/{run_id}/idea-discovery/hypotheses/{finalists[-1]}/reject",
                json={
                    "actor": "e2e-researcher",
                    "reason": "superseded by a more falsifiable mechanism",
                },
            )
            assert edited.status_code == 200, edited.text
            assert rejected.status_code == 200, rejected.text
            mutated = client.get(f"/api/runs/{run_id}/idea-discovery").json()
            assert mutated["finalist_ids"][0] == human_id
            assert finalists[-1] not in mutated["finalist_ids"]
            human = next(
                item for item in mutated["hypotheses"] if item["hypothesis_id"] == human_id
            )
            assert human["operator"] == "human_edit"
            assert human["statement"].startswith("A human-edited")
            rejected_hypothesis = next(
                item
                for item in mutated["hypotheses"]
                if item["hypothesis_id"] == finalists[-1]
            )
            assert rejected_hypothesis["blocked"] is True

            selection_payload = {
                "actor": "e2e-researcher",
                "reason": "best bounded deterministic hypothesis",
                "idempotency_key": "idea-selection-e2e-1",
            }
            first = client.post(
                f"/api/runs/{run_id}/idea-discovery/hypotheses/{human_id}/select",
                json=selection_payload,
            )
            assert first.status_code == 200, first.text
            assert first.json()["status"] == "completed"
            proposal_ref = str(first.json()["proposal_ref"])
            run = store.get(run_id)
            assert run is not None
            proposal_path = run.root / proposal_ref
            assert validate_document(
                proposal_path.read_text(encoding="utf-8"),
                expected_schema="proposal.v1",
            ).valid
            metadata = parse_frontmatter(
                proposal_path.read_text(encoding="utf-8")
            ).metadata
            summary = metadata["discovery_summary"]
            assert isinstance(summary, dict)
            assert summary["selected_hypothesis_id"] == human_id
            assert summary["selection_actor"] == "e2e-researcher"
            assert summary["selection_source"] == "human"
            selection_path = run.root / "idea" / "discovery" / "selection.v1.json"
            selection_bytes = selection_path.read_bytes()
            versions_before = ArtifactStore(run).list_versions(
                agent_dir="idea",
                stem="idea_proposal",
            )

            repeated = client.post(
                f"/api/runs/{run_id}/idea-discovery/hypotheses/{human_id}/select",
                json=selection_payload,
            )

            assert repeated.status_code == 200, repeated.text
            assert repeated.json() == first.json()
            assert selection_path.read_bytes() == selection_bytes
            assert ArtifactStore(run).list_versions(
                agent_dir="idea",
                stem="idea_proposal",
            ) == versions_before
    finally:
        deps.reset_for_tests()
        reset_registry_for_tests()
        reset_settings_cache()
        reset_extension_runtime()
