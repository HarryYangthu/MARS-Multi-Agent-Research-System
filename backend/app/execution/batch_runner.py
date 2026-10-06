"""Concurrent batch runner with a configurable cap (V0 default = 16)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.execution.results import SimulationResult
from app.execution.simulation_runner import JobSpec
from app.execution.job_journal import run_managed_job


@dataclass
class BatchConfig:
    max_concurrency: int = 16
    steps: int = 30


@dataclass
class BatchOutcome:
    results: list[SimulationResult] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)


async def run_batch(
    specs: list[JobSpec],
    *,
    config: BatchConfig | None = None,
    bus_publish: Any | None = None,
    check_inputs: Any | None = None,
) -> BatchOutcome:
    cfg = config or BatchConfig()
    if cfg.max_concurrency < 1 or cfg.steps < 1:
        raise ValueError("batch concurrency and steps must be positive")
    sem = asyncio.Semaphore(cfg.max_concurrency)
    outcome = BatchOutcome()

    async def runner(s: JobSpec) -> None:
        async with sem:
            try:
                if check_inputs is not None:
                    await check_inputs()
                res = await run_managed_job(s, bus_publish=bus_publish, steps=cfg.steps)
                outcome.results.append(res)
                if res.status != "completed":
                    outcome.failures.append((s.experiment_id, f"execution status: {res.status}"))
            except Exception as exc:
                logger.exception("batch job {} failed", s.experiment_id)
                outcome.failures.append((s.experiment_id, str(exc)))

    # TaskGroup waits for every child's process cleanup before acknowledging stop.
    async with asyncio.TaskGroup() as group:
        for spec in specs:
            group.create_task(runner(spec), name=f"execution:{spec.experiment_id}")
    return outcome
