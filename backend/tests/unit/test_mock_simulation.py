from __future__ import annotations

import asyncio

import pytest

from app.execution.mock_simulation import MockJob, run_mock_simulation
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.validator import validate_metadata


@pytest.mark.asyncio
async def test_run_returns_completed() -> None:
    job = MockJob(run_id="r1", experiment_id="exp1", project="moe-pimc", seed=42)
    result = await run_mock_simulation(job, sleep_per_tick=0.0, steps=5)
    assert result.status == "completed"
    assert result.fingerprint_hash.startswith("sha256:")


@pytest.mark.asyncio
async def test_publishes_curve_points() -> None:
    received: list[dict] = []  # type: ignore[type-arg]

    async def pub(channel: str, payload: dict) -> None:  # type: ignore[type-arg]
        received.append({"channel": channel, **payload})

    job = MockJob(run_id="r1", experiment_id="exp1", project="moe-pimc", seed=42)
    await run_mock_simulation(job, bus_publish=pub, sleep_per_tick=0.0, steps=5)
    assert any(r.get("event") == "execution.started" for r in received)
    assert any(r.get("event") == "execution.curve_point" for r in received)
    assert any(r.get("event") == "execution.completed" for r in received)


@pytest.mark.asyncio
async def test_result_metadata_validates_run_log_v1() -> None:
    job = MockJob(run_id="r1", experiment_id="exp1", project="moe-pimc", seed=42)
    result = await run_mock_simulation(job, sleep_per_tick=0.0, steps=3)
    metadata = {
        "schema": "run_log.v1",
        "project": "moe-pimc",
        "agent": "execution",
        "run_id": f"{result.run_id}_{result.experiment_id}",
        "status": result.status,
        "metrics": result.metrics,
        "fingerprint_hash": result.fingerprint_hash,
        "is_mock": True,
    }
    res = validate_metadata(metadata, expected_schema="run_log.v1")
    assert res.valid, res.errors
