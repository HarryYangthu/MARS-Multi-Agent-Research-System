"""Actual process ownership remains cancellable when persisted records fail."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.api import dependencies
from app.bridge.agent_registry import AgentRegistry
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.main import create_app
from app.storage.run_store import RunStore


@pytest.mark.asyncio
async def test_control_of_unknown_run_does_not_create_owner_or_storage(tmp_path: Path) -> None:
    dependencies.reset_for_tests()
    try:
        # App composition creates its actual store. Isolate that setup, then
        # measure the endpoint itself from an empty dependency registry.
        dependencies._run_store = RunStore(tmp_path / "runs")
        app = create_app()
        dependencies.reset_for_tests()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            result = await client.get("/api/runs/unknown-process-task/control")
        assert result.status_code == 200
        assert result.json() == {"run_id": "unknown-process-task", "owned_task_active": False,
            "stopping": False, "owned_task_done": None, "cleanup_complete": None, "state_persisted": None,
            "state_persistence_error": None, "available_actions": []}
        assert dependencies.existing_orchestrator() is None and dependencies._run_store is None
    finally:
        dependencies.reset_for_tests()


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["readonly", "corrupt_db", "missing_db", "corrupt_meta", "missing_meta", "discarded_session"])
async def test_actual_api_control_and_stop_survive_storage_failure(tmp_path: Path, damage: str) -> None:
    dependencies.reset_for_tests()
    store = RunStore(tmp_path / "runs")
    owner = Orchestrator(run_store=store, registry=AgentRegistry())
    session = owner.create_session(RunRequest(task="Actual cancellation boundary", project="synthetic_regression",
                                              entrypoint="idea", standalone=True))
    dependencies._run_store = store
    dependencies._orchestrator = owner
    run_id = session.run.run_id
    started, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))

    async def actual_owned_cleanup() -> None:
        # This is a real cancellable process task, never a substitute Agent or
        # provider. No stage result or model/tool execution is claimed.
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            cleaned.set()

    assert owner._spawn_owned(session, "control-api-cancellation", actual_owned_cleanup)
    task = owner.owned_tasks.active(run_id)
    assert task is not None
    await started.wait()
    if damage == "readonly":
        session.read_only = True
        session.read_only_reason = "state_persistence_error"
    elif damage in {"corrupt_db", "missing_db"}:
        path = session.run.root / "run_state.sqlite3"
        if damage == "missing_db":
            path.unlink()
        else:
            path.write_bytes(b"Invalid SQLite: cancellation must not depend on reading this")
    elif damage in {"corrupt_meta", "missing_meta"}:
        path = session.run.root / "run_meta.json"
        if damage == "missing_meta":
            path.unlink()
        else:
            path.write_text("{")
    else:
        owner.discard_session(run_id)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
            control = await client.get(f"/api/runs/{run_id}/control")
            assert control.status_code == 200
            assert control.json()["owned_task_active"] is True
            assert control.json()["available_actions"] == ["stop"]
            if damage == "readonly":
                detail = await client.get(f"/api/runs/{run_id}")
                assert detail.status_code == 200 and detail.json()["read_only"] is True
                assert detail.json()["available_actions"] == ["stop"]
            elif damage in {"corrupt_db", "missing_db"}:
                detail = await client.get(f"/api/runs/{run_id}")
                assert detail.status_code == 409
                assert detail.json()["detail"]["control"]["available_actions"] == ["stop"]
            stopper = asyncio.create_task(client.post(f"/api/runs/{run_id}/stop"))
            try:
                await asyncio.wait_for(cleaning.wait(), timeout=1)
                pending = (await client.get(f"/api/runs/{run_id}/control")).json()
                assert pending["owned_task_active"] and pending["stopping"]
                assert pending["owned_task_done"] is False and pending["available_actions"] == ["stop"]
                assert task.cancelling() == 1
                release.set()
                stopped = await asyncio.wait_for(stopper, timeout=2)
            finally:
                release.set()
                await stopper
            assert cleaned.is_set() and owner.owned_tasks.active(run_id) is None
            assert stopped.status_code == (202 if damage in {"corrupt_meta", "missing_meta"} else 409)
            if stopped.status_code == 409:
                assert stopped.json()["detail"]["status"] == "stop_state_error"
                assert stopped.json()["detail"]["owned_task_done"] is True
                assert stopped.json()["detail"]["state_persisted"] is False
            final = (await client.get(f"/api/runs/{run_id}/control")).json()
            assert final["owned_task_active"] is False and final["owned_task_done"] is True
            assert final["available_actions"] == []
            assert not (session.run.root / "agent_traces").exists()
    finally:
        release.set()
        owner.owned_tasks.cancel_once(run_id)
        assert await owner.owned_tasks.wait(task, timeout=2)
        dependencies.reset_for_tests()


@pytest.mark.asyncio
async def test_historical_no_owner_is_not_reported_as_successfully_cancelled(tmp_path: Path) -> None:
    dependencies.reset_for_tests()
    store = RunStore(tmp_path / "runs")
    run = store.create(task="Historical metadata only", project="synthetic_regression")
    dependencies._run_store = store
    try:
        original = (run.root / "run_meta.json").read_bytes()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
            control = (await client.get(f"/api/runs/{run.run_id}/control")).json()
            assert control["owned_task_active"] is False and control["owned_task_done"] is None
            response = await client.post(f"/api/runs/{run.run_id}/stop")
            assert response.status_code == 409 and response.json()["detail"]["status"] == "not_owned"
        assert dependencies.existing_orchestrator() is None
        assert (run.root / "run_meta.json").read_bytes() == original
        assert not (run.root / "run_state.sqlite3").exists()
    finally:
        dependencies.reset_for_tests()
