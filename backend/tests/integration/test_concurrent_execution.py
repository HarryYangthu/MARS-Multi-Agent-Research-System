"""Concurrent real CPU PIM fits, observed through their actual event channels."""
from pathlib import Path
from typing import Any
import pytest
from app.execution.batch_runner import BatchConfig, run_batch
from app.execution.simulation_runner import JobSpec
from app.settings import reset_settings_cache


@pytest.mark.asyncio
@pytest.mark.parametrize("cap,count", [(6, 6), (2, 3)])
async def test_real_cpu_jobs_obey_cap_and_isolate_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      cap: int, count: int) -> None:
    monkeypatch.setenv("MARS_EXECUTION_BACKEND", "pim_cpu")
    reset_settings_cache()
    active: set[str] = set()
    peak = 0
    events: dict[str, list[dict[str, Any]]] = {}
    async def observe(channel: str, payload: dict[str, Any]) -> None:
        nonlocal peak
        events.setdefault(channel, []).append(payload)
        if payload["event"] == "execution.started":
            active.add(channel)
            peak = max(peak, len(active))
        elif payload["event"] in {"execution.completed", "execution.failed"}:
            active.remove(channel)
    specs = [JobSpec(run_id="real-batch", experiment_id=f"fit_{i}", project="pimc", seed=i,
                     run_root=tmp_path / str(i), plot_every_steps=60) for i in range(count)]
    try:
        outcome = await run_batch(specs, config=BatchConfig(max_concurrency=cap, steps=60), bus_publish=observe)
    finally:
        reset_settings_cache()
    assert not outcome.failures
    assert len(outcome.results) == count
    assert 1 < peak <= cap and not active
    assert len(events) == count
    for result in outcome.results:
        assert result.status == "completed" and not result.is_mock
        assert result.duration_seconds > 0 and result.metrics["loss"] >= 0
        channel = f"run.real-batch.experiment.{result.experiment_id}"
        assert all(row["experiment_id"] == result.experiment_id for row in events[channel])
        assert sum(row["event"] == "execution.started" for row in events[channel]) == 1
        assert sum(row["event"] == "execution.completed" for row in events[channel]) == 1
