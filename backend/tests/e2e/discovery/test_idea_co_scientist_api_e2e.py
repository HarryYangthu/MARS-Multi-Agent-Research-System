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
from app.main import create_app
from app.settings import reset_settings_cache
from app.storage.run_store import RunStore


def test_main_app_reports_unwired_deep_mode_without_invented_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARS_MOCK_MODE", "never")
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
                    "project_inputs": {"mode": "synthetic"},
                },
            )
            assert created.status_code == 200, created.text
            run_id = str(created.json()["run_id"])
            started = client.post(f"/api/runs/{run_id}/start")
            assert started.status_code == 202, started.text

            # Deep mode is explicitly unavailable in the audited native loop.
            # Starting it must not manufacture a hypothesis pool or proposal.
            for _ in range(100):
                detail = client.get(f"/api/runs/{run_id}")
                assert detail.status_code == 200
                if detail.json().get("states", {}).get("idea") == "failed":
                    break
                time.sleep(0.05)
            assert detail.json()["states"]["idea"] == "failed"
            overview = client.get(f"/api/runs/{run_id}/idea-discovery").json()
            assert overview["hypotheses"] == []
            run = store.get(run_id)
            assert run is not None
            assert not list(run.root.rglob("*.approved.md"))
            assert not list(run.root.rglob("hypotheses.v1.json"))
    finally:
        deps.reset_for_tests()
        reset_registry_for_tests()
        reset_settings_cache()
        reset_extension_runtime()
