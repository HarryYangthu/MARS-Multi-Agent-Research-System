"""End-to-end FastAPI test: create a run, list it, fetch detail."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import reset_for_tests
from app.main import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Force RunStore + Orchestrator to use a fresh tmpdir-rooted store.
    monkeypatch.chdir(tmp_path)
    # repo_root() looks at module path, but RunStore default uses repo_root().
    # We patch the dependency provider directly instead.
    reset_for_tests()
    from app.api import dependencies as deps
    from app.bridge.orchestrator import Orchestrator
    from app.harness.runtime.event_bus import InProcessEventBus
    from app.storage.run_store import RunStore

    store = RunStore(tmp_path / "runs")
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=store, bus=bus)

    deps._run_store = store
    deps._bus = bus
    deps._orchestrator = orch

    app = create_app()
    return TestClient(app)


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_create_and_list_run(client: TestClient) -> None:
    r = client.post(
        "/api/runs",
        json={"task": "smoke", "project": "moe-pimc", "entrypoint": "pipeline"},
    )
    assert r.status_code == 200, r.text
    detail = r.json()
    run_id = detail["run_id"]
    assert detail["entrypoint"] == "pipeline"
    assert set(detail["states"]) == {"idea", "experiment", "coding", "execution", "writing"}

    r2 = client.get("/api/runs")
    assert r2.status_code == 200
    assert any(item["run_id"] == run_id for item in r2.json())

    r3 = client.get(f"/api/runs/{run_id}")
    assert r3.status_code == 200
    assert r3.json()["run_id"] == run_id


def test_get_unknown_run_returns_404(client: TestClient) -> None:
    r = client.get("/api/runs/no-such")
    assert r.status_code == 404
