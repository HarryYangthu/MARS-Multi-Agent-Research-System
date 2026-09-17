"""Real generated-signal computation, and actual worker lifetime boundaries."""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.execution.simulation_runner import JobSpec, run_real_pim_simulation


def _spec(root: Path, name: str) -> JobSpec:
    return JobSpec(run_id="cpu-worker", experiment_id=name, project="pimc", run_root=root,
        seed=11, plot_every_steps=1000,
        config={"n_points": 1024, "memory": 2, "order": 3, "loss_batch_size": 128})


def _assert_reaped(root: Path, name: str) -> dict[str, Any]:
    process_path = next((root / "execution/pim_cpu" / name).glob("*/process.json"))
    pid = json.loads(process_path.read_text())["pid"]
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    return dict(json.loads((process_path.parent / "execution_receipt.json").read_text()))


@pytest.mark.asyncio
async def test_worker_returns_actual_computed_metrics(tmp_path: Path) -> None:
    result = await run_real_pim_simulation(_spec(tmp_path, "measured"), steps=8, sleep_per_tick=0)
    assert result.status == "completed" and not result.is_mock
    assert len(result.loss_curve) == 8 and result.loss_curve[0] > result.loss_curve[-1]
    assert all(math.isfinite(value) for value in result.metrics.values())
    receipt = _assert_reaped(tmp_path, "measured")
    assert receipt["returncode"] == 0 and receipt["status"] == "completed"
    assert receipt["result_sha256"].startswith("sha256:")


@pytest.mark.skipif(os.name != "posix", reason="POSIX process reaping check")
@pytest.mark.asyncio
async def test_cancel_after_real_training_step_stops_worker(tmp_path: Path) -> None:
    observed_step = asyncio.Event()

    async def publish(_channel: str, event: dict[str, Any]) -> None:
        if event.get("event") == "execution.curve_point":
            observed_step.set()

    task = asyncio.create_task(run_real_pim_simulation(_spec(tmp_path, "cancel"), bus_publish=publish,
                                                      steps=1000, sleep_per_tick=0.1))
    try:
        await asyncio.wait_for(observed_step.wait(), timeout=20)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    receipt = _assert_reaped(tmp_path, "cancel")
    assert receipt["status"] == "cancelled" and receipt["returncode"] != 0


@pytest.mark.skipif(os.name != "posix", reason="POSIX process reaping check")
@pytest.mark.asyncio
async def test_actual_worker_timeout_cannot_leave_background_training(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "host-execution.yaml"
    config_path.write_text(yaml.safe_dump({"execution": {"pim_cpu_timeout_seconds": 0.1}}))
    monkeypatch.setenv("MARS_EXECUTION_CONFIG_PATH", str(config_path))
    result = await run_real_pim_simulation(_spec(tmp_path, "timeout"), steps=1000, sleep_per_tick=0.1)
    assert result.status == "failed" and result.metrics == {}
    receipt = _assert_reaped(tmp_path, "timeout")
    assert receipt["returncode"] != 0 and "TimeoutError" in receipt["error"]
