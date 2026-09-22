"""Real event files + a real TensorBoard server; no provider/tool substitutes."""
from __future__ import annotations

import asyncio
from pathlib import Path
import time

import httpx
import pytest
from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

from app.bridge.tensorboard_service import TensorBoardManager, scope_key
from app.execution.tensorboard_writer import ExecutionScalars, ScalarWriter
from app.storage.run_store import RunStore


def test_scalar_events_preserve_values_steps_and_experiment_identity(tmp_path: Path) -> None:
    sink = ExecutionScalars(tmp_path)
    sink.record({"event": "execution.started", "experiment_id": "a"})
    assert not list(tmp_path.rglob("events.out.tfevents*"))
    for name, value in (("a/b", 0.125), ("a_b", 0.5)):
        sink.record({"event": "execution.curve_point", "experiment_id": name,
                     "step": 7, "metric": "loss", "value": value})
    sink.close()
    folders = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert len(folders) == 2
    records = [EventAccumulator(str(path)).Reload().Scalars("train/loss")[0] for path in folders]
    assert {record.step for record in records} == {7}
    assert {record.value for record in records} == {0.125, 0.5}


def test_writer_omits_nonfinite_and_nonmeasurement_values(tmp_path: Path) -> None:
    with ScalarWriter(tmp_path) as writer:
        for value in (float("nan"), float("inf"), True, "1"):
            writer.scalar("invalid", value, 0)
        writer.scalar("validation/RES_db", 8.5, 42)
    events = EventAccumulator(str(tmp_path)).Reload()
    assert events.Tags()["scalars"] == ["validation/RES_db"]
    assert events.Scalars("validation/RES_db")[0].step == 42


@pytest.mark.asyncio
async def test_real_tensorboard_loads_and_reloads_run_scoped_events(tmp_path: Path) -> None:
    manager = TensorBoardManager(tmp_path / "service")
    run = RunStore(tmp_path / "runs").create(task="TensorBoard integration", project="pimc")
    event_dir = run.subdir("execution") / "tensorboard"
    try:
        with ScalarWriter(event_dir) as writer:
            writer.scalar("train/loss", 0.25, 1)
            writer.flush()
            activation = await manager.activate(run, 1)
            session = manager.sessions[str(activation["key"])]
            same = await manager.ensure(run.project, run.run_id, [run.root])
            assert same.process.pid == session.process.pid
            assert activation["phase"] == "running"
            assert scope_key("other", run.run_id) != session.key
            base = f"http://127.0.0.1:{session.port}{session.prefix}"
            async with httpx.AsyncClient(trust_env=False) as client:
                page = await client.get(base + "/")
                assert page.status_code == 200 and "tensorboard" in page.text.lower()
                writer.scalar("train/loss", 0.125, 2)
                writer.flush()
                deadline = time.monotonic() + 15
                rows: list[list[float]] = []
                while time.monotonic() < deadline:
                    response = await client.get(base + "/data/plugin/scalars/scalars",
                        params={"run": "execution/tensorboard", "tag": "train/loss"})
                    if response.status_code == 200:
                        rows = response.json()
                        if rows and rows[-1][1] == 2:
                            break
                    await asyncio.sleep(0.5)
                assert rows[-1][1:] == [2, 0.125]
            manager.finish(run, "completed")
            assert manager.activations["pimc"]["phase"] == "completed"
    finally:
        await manager.close()
    assert session.process.returncode is not None


@pytest.mark.asyncio
async def test_missing_directory_cannot_launch_a_viewer(tmp_path: Path) -> None:
    manager = TensorBoardManager(tmp_path / "service")
    with pytest.raises(ValueError):
        await manager.ensure("pimc", None, [tmp_path / "missing"])
    assert manager.sessions == {}


@pytest.mark.asyncio
async def test_proxy_directory_routes_do_not_redirect_to_backend_origin() -> None:
    from fastapi import FastAPI
    from app.api.tensorboard import router

    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for path in ("/api/tensorboard/view/unknown", "/api/tensorboard/view/unknown/"):
            response = await client.get(path)
            assert response.status_code == 503
            assert "location" not in response.headers
