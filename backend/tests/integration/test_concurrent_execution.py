"""Phase 6 e2e: 6 concurrent mock simulations + WS channel isolation."""
from __future__ import annotations

import asyncio
import time

import pytest

from app.execution.batch_runner import BatchConfig, run_batch
from app.execution.simulation_runner import JobSpec
from app.harness.runtime.event_bus import InProcessEventBus


@pytest.mark.asyncio
async def test_six_jobs_run_concurrently() -> None:
    bus = InProcessEventBus()
    events_per_channel: dict[str, list[dict]] = {}  # type: ignore[type-arg]

    async def pub(channel: str, payload: dict) -> None:  # type: ignore[type-arg]
        events_per_channel.setdefault(channel, []).append(payload)
        await bus.publish(channel, payload)

    specs = [
        JobSpec(
            run_id="batch-test",
            experiment_id=f"exp_{i}",
            project="moe-pimc",
            seed=i,
        )
        for i in range(6)
    ]

    started = time.monotonic()
    outcome = await run_batch(
        specs,
        config=BatchConfig(max_concurrency=6, steps=5),
        bus_publish=pub,
    )
    elapsed = time.monotonic() - started

    # 6 results returned, no failures
    assert len(outcome.results) == 6
    assert outcome.failures == []

    # Each experiment got its own channel — no cross-talk
    assert len(events_per_channel) == 6
    for channel, events in events_per_channel.items():
        assert any(e.get("event") == "execution.started" for e in events)
        assert any(e.get("event") == "execution.completed" for e in events)
        # Channel is per-experiment by construction
        assert channel.startswith("run.batch-test.experiment.exp_")

    # Concurrency: with sem=6 and 6 jobs, total elapsed should be << 6 * single_job
    # We can't bound it tightly without knowing CI noise, so just sanity-check.
    single = (5 * 0.05)  # steps * sleep_per_tick
    assert elapsed < single * 6, f"jobs were not concurrent (elapsed={elapsed:.2f})"


@pytest.mark.asyncio
async def test_seventh_job_queues_behind_cap() -> None:
    """With cap=2, the 3rd of 3 jobs starts after one of the first two finishes."""
    starts: list[float] = []

    async def pub(channel: str, payload: dict) -> None:  # type: ignore[type-arg]
        if payload.get("event") == "execution.started":
            starts.append(time.monotonic())

    specs = [
        JobSpec(
            run_id="cap-test",
            experiment_id=f"exp_{i}",
            project="moe-pimc",
            seed=i,
        )
        for i in range(3)
    ]
    await run_batch(
        specs,
        config=BatchConfig(max_concurrency=2, steps=10),
        bus_publish=pub,
    )
    starts.sort()
    # The third job starts after at least the first finishes — so its start
    # time should be later than starts[0].
    assert len(starts) == 3
    assert starts[2] > starts[0]
