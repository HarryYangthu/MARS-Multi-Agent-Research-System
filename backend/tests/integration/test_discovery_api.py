from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.discovery import (
    configure_discovery_service,
    configure_idea_selection_handler,
    router,
)
from app.bridge.discovery_service import DiscoveryService, IdeaSelectionRequest
from app.harness.runtime.event_bus import InProcessEventBus
from app.storage.run_store import RunStore
from tests.unit.bridge.test_discovery_service import (
    FakeAdapter,
    FakeCandidateAgent,
    discovery_spec,
)


def test_discovery_api_and_run_local_idea_selection(tmp_path: Path) -> None:
    configure_idea_selection_handler(None)
    service = DiscoveryService(
        run_store=RunStore(tmp_path / "runs"),
        event_bus=InProcessEventBus(),
        candidate_agent=FakeCandidateAgent(),
        adapter=FakeAdapter(fail_index=None),
    )
    configure_discovery_service(service)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/api/discovery/runs",
        json={
            "spec": discovery_spec(candidates=2).model_dump(mode="json"),
            "idempotency_key": "api-create",
        },
    )
    assert response.status_code == 200
    run_id = str(response.json()["run_id"])

    started = client.post(
        f"/api/discovery/runs/{run_id}/start",
        json={"wait": True},
    )
    assert started.status_code == 200
    assert started.json()["lifecycle"] == "completed"
    assert started.json()["candidate_count"] == 2

    replay = client.get(f"/api/discovery/runs/{run_id}/replay")
    assert replay.status_code == 200
    assert len(replay.json()["evaluations"]) == 2

    empty_idea = client.get(f"/api/runs/{run_id}/idea-discovery")
    assert empty_idea.status_code == 200
    assert empty_idea.json()["hypotheses"] == []
    assert empty_idea.json()["reflections"] == []
    assert empty_idea.json()["matches"] == []
    assert empty_idea.json()["finalist_ids"] == []
    assert empty_idea.json()["selection"] is None

    run = service.run_store.get(run_id)
    assert run is not None
    hypothesis_dir = run.root / "idea" / "discovery"
    hypothesis_dir.mkdir(parents=True, exist_ok=True)
    (hypothesis_dir / "hypothesis_pool.v1.json").write_text(
        json.dumps(
            {
                "schema_id": "hypothesis_pool.v1",
                "run_id": run_id,
                "project": "synthetic_regression",
                "status": "waiting_selection",
                "config": {
                    "budget_profile": "balanced",
                    "initial_hypotheses": 2,
                    "evolution_rounds": 1,
                    "max_pairwise_matches": 2,
                    "top_k": 2,
                },
                "top_hypothesis_ids": ["hyp-2", "hyp-1"],
                "selected_hypothesis_id": "",
            }
        ),
        encoding="utf-8",
    )
    wrappers: dict[str, list[dict[str, object]]] = {
        "hypotheses.v1.json": [
            {
                "schema_id": "hypothesis.v1",
                "hypothesis_id": "hyp-1",
                "round_index": 0,
                "statement": "A synthetic feature improves validation loss.",
            },
            {
                "schema_id": "hypothesis.v1",
                "hypothesis_id": "hyp-2",
                "round_index": 1,
                "statement": "A bounded feature schedule improves validation loss.",
            },
        ],
        "reflections.v1.json": [
            {"reflection_id": "reflection-1", "hypothesis_id": "hyp-1"}
        ],
        "pairwise_matches.v1.json": [
            {
                "match_id": "match-1",
                "left_id": "hyp-1",
                "right_id": "hyp-2",
                "outcome": "right",
            }
        ],
        "proximity_graphs.v1.json": [
            {"round_index": 1, "clusters": {"cluster-1": ["hyp-1", "hyp-2"]}}
        ],
        "meta_reviews.v1.json": [
            {"meta_review_id": "meta-1", "round_index": 1}
        ],
    }
    for name, items in wrappers.items():
        (hypothesis_dir / name).write_text(
            json.dumps(
                {
                    "schema_id": name.removesuffix(".json"),
                    "count": len(items),
                    "items": items,
                }
            ),
            encoding="utf-8",
        )
    (hypothesis_dir / "state.v1.json").write_text(
        json.dumps(
            {
                "schema_id": "idea_deep_discovery_state.v1",
                "status": "waiting_selection",
                "backend_mode": "deterministic_mock",
            }
        ),
        encoding="utf-8",
    )

    hypotheses = client.get(f"/api/runs/{run_id}/idea-discovery/hypotheses")
    assert hypotheses.status_code == 200
    assert len(hypotheses.json()["hypotheses"]) == 2
    assert hypotheses.json()["hypotheses"][0]["hypothesis_id"] == "hyp-1"

    selection_payload = {"hypothesis_id": "hyp-1", "idempotency_key": "select-1"}
    selected = client.post(
        f"/api/runs/{run_id}/idea-discovery/select",
        json=selection_payload,
    )
    repeated = client.post(
        f"/api/runs/{run_id}/idea-discovery/select",
        json=selection_payload,
    )
    assert selected.status_code == 200
    assert repeated.json() == selected.json()
    assert selected.json()["schema_id"] == "idea_discovery_selection_request.v1"
    request_ref = str(selected.json()["selection_request_ref"])
    assert (run.root / request_ref).exists()

    async def materialize_selection(_: IdeaSelectionRequest) -> str:
        return "idea/idea_proposal.v2.md"

    configure_idea_selection_handler(materialize_selection)
    action_selected = client.post(
        f"/api/runs/{run_id}/idea-discovery/hypotheses/hyp-1/select",
        json={"actor": "researcher", "reason": "best synthetic hypothesis"},
    )
    assert action_selected.status_code == 200
    assert action_selected.json()["actor"] == "researcher"
    assert action_selected.json()["status"] == "completed"
    assert action_selected.json()["proposal_ref"] == "idea/idea_proposal.v2.md"
    configure_idea_selection_handler(None)

    (hypothesis_dir / "selection.v1.json").write_text(
        json.dumps(
            {
                "schema_id": "hypothesis_selection.v1",
                "selection_id": "selection-1",
                "run_id": run_id,
                "hypothesis_id": "hyp-2",
                "actor": "researcher",
                "reason": "best Top-K hypothesis",
                "source": "human",
                "proposal_metadata": {"schema": "proposal.v1"},
                "proposal_body": "Selected proposal body.",
            }
        ),
        encoding="utf-8",
    )

    overview = client.get(f"/api/runs/{run_id}/idea-discovery")
    assert overview.status_code == 200
    payload = overview.json()
    assert payload["idea_mode"] == "auto"
    assert payload["project"] == "synthetic_regression"
    assert payload["status"] == "selected"
    assert payload["backend_mode"] == "deterministic_mock"
    assert payload["config"]["top_k"] == 2
    assert len(payload["hypotheses"]) == 2
    assert len(payload["reflections"]) == 1
    assert len(payload["matches"]) == 1
    assert len(payload["proximity_graphs"]) == 1
    assert len(payload["meta_reviews"]) == 1
    assert payload["finalist_ids"] == ["hyp-2", "hyp-1"]
    assert payload["selected_id"] == "hyp-2"
    assert payload["proposal_ref"] == ""
    assert payload["selection"]["hypothesis_id"] == "hyp-2"
    assert "hypothesis_pool.v1.json" in payload["artifacts"]

    configure_discovery_service(None)
