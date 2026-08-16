from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.discovery import configure_discovery_service, router
from app.bridge.discovery_service import DiscoveryService
from app.harness.runtime.event_bus import InProcessEventBus
from app.storage.run_store import RunStore
from tests.unit.bridge.test_discovery_service import (
    FakeAdapter,
    FakeCandidateAgent,
    discovery_spec,
)


def test_discovery_api_and_run_local_idea_selection(tmp_path: Path) -> None:
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

    run = service.run_store.get(run_id)
    assert run is not None
    hypothesis_dir = run.root / "idea" / "discovery"
    hypothesis_dir.mkdir(parents=True, exist_ok=True)
    (hypothesis_dir / "hypotheses.json").write_text(
        json.dumps(
            {
                "hypotheses": [
                    {
                        "schema_id": "hypothesis.v1",
                        "hypothesis_id": "hyp-1",
                        "statement": "A synthetic feature improves validation loss.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    hypotheses = client.get(f"/api/runs/{run_id}/idea-discovery/hypotheses")
    assert hypotheses.status_code == 200
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

    action_selected = client.post(
        f"/api/runs/{run_id}/idea-discovery/hypotheses/hyp-1/select",
        json={"actor": "researcher", "reason": "best synthetic hypothesis"},
    )
    assert action_selected.status_code == 200
    assert action_selected.json()["actor"] == "researcher"

    (hypothesis_dir / "selection.v1.json").write_text(
        json.dumps(
            {
                "schema_id": "hypothesis_selection.v1",
                "hypothesis_id": "hyp-1",
                "proposal_ref": "idea/proposal.v1.md",
            }
        ),
        encoding="utf-8",
    )

    overview = client.get(f"/api/runs/{run_id}/idea-discovery")
    assert overview.status_code == 200
    assert overview.json()["idea_mode"] == "auto"
    assert overview.json()["selection"]["hypothesis_id"] == "hyp-1"

    configure_discovery_service(None)
