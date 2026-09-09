from __future__ import annotations

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


@pytest.mark.parametrize("mode", ["auto", "deep"])
def test_main_app_rejects_unwired_mode_before_creating_a_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
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
                    "idea_mode": mode,
                    "idea_budget_profile": "balanced",
                    "project_inputs": {"mode": "synthetic"},
                },
            )
            assert created.status_code == 422, created.text
            error = created.json()["detail"][0]
            assert error["loc"] == ["body", "idea_mode"]
            assert "use fast or omit idea_mode" in error["msg"]
            assert store.list() == []
    finally:
        deps.reset_for_tests()
        reset_registry_for_tests()
        reset_settings_cache()
        reset_extension_runtime()
