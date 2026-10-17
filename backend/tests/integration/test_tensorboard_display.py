"""Real event files + a real TensorBoard server; no provider/tool substitutes."""
from __future__ import annotations

import asyncio
from pathlib import Path
import time

import httpx
import pytest
from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

from app.bridge.tensorboard_service import TensorBoardManager, get_tensorboard_manager, scope_key, shutdown_tensorboard
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


def test_idle_writer_does_not_create_empty_event_files(tmp_path: Path) -> None:
    with ScalarWriter(tmp_path / 'idle') as writer:
        writer.scalar('invalid', float('nan'), 0)
        writer.flush()
    assert not list(tmp_path.rglob('events.out.tfevents.*'))


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
                        params={"run": str(event_dir.relative_to(run.root)), "tag": "train/loss"})
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
async def test_other_application_loop_cannot_close_owned_tensorboard(tmp_path: Path) -> None:
    manager = get_tensorboard_manager()
    try:
        session = await manager.ensure("pimc", "loop-ownership", [tmp_path])
        await asyncio.to_thread(asyncio.run, shutdown_tensorboard())
        assert get_tensorboard_manager() is manager
        assert session.process.returncode is None
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(f"http://127.0.0.1:{session.port}{session.prefix}/data/environment")
        assert response.status_code == 200
    finally:
        await shutdown_tensorboard()
    assert session.process.returncode is not None


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


@pytest.mark.asyncio
@pytest.mark.parametrize('format', ['history', 'circle_csv'])
async def test_running_viewer_collects_new_saved_observations(tmp_path: Path, format: str) -> None:
    import json

    manager = TensorBoardManager(tmp_path/'service')
    run = RunStore(tmp_path/'runs').create(task='continuous observability', project='pimc')
    attempt = run.root/'execution/local_commands/study/invocation'
    model = attempt/'training/model'
    model.mkdir(parents=True)
    (attempt/'job.json').write_text(json.dumps({'run_id': run.run_id, 'experiment_id': 'study'}))
    if format == 'history':
        (model/'history.json').write_text('[{"optimizer_steps": 0, "validation_RES_db": 9.0}]')
        (model/'steps.jsonl').write_text('')
    else:
        (model/'training.csv').write_text(
            'circle,updates,lr,mean_loss_sum,train_PIM_native,train_RES_native,rx_centers_mhz,err_centers_mhz\n')
    tag = 'train/loss' if format == 'history' else 'train/mean_loss_sum'
    try:
        await manager.activate(run, 1)
        if format == 'history':
            (model/'steps.jsonl').write_text('{"optimizer_step": 1, "training_loss": 0.125}\n')
        else:
            with (model/'training.csv').open('a') as handle:
                handle.write('1,1,0.004,0.125,25.84,22,[34.3],[34.2]\n')
        deadline = time.monotonic()+10
        records = []
        while time.monotonic() < deadline:
            dirs = list((run.root/'execution/tensorboard_artifacts').glob('*/training/model'))
            if dirs:
                accumulator = EventAccumulator(str(dirs[0])).Reload()
                if tag in accumulator.Tags()['scalars']:
                    records = accumulator.Scalars(tag)
                    if records:
                        break
            await asyncio.sleep(.25)
        assert records[0].value == .125 and records[0].step == 1
        manager.finish(run, 'completed')
        assert run.run_id not in manager.artifact_watchers
    finally:
        await manager.close()
