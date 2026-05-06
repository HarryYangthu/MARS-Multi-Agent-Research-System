"""Concurrent batch runner with a configurable cap (V0 default = 6)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.execution.mock_simulation import MockResult
from app.execution.simulation_runner import JobSpec, run_one


@dataclass
class BatchConfig:
    max_concurrency: int = 6
    steps: int = 30


@dataclass
class BatchOutcome:
    results: list[MockResult] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)


async def run_batch(
    specs: list[JobSpec],
    *,
    config: BatchConfig | None = None,
    bus_publish: Any | None = None,
) -> BatchOutcome:
    cfg = config or BatchConfig()
    sem = asyncio.Semaphore(cfg.max_concurrency)
    outcome = BatchOutcome()

    async def runner(s: JobSpec) -> None:
        async with sem:
            try:
                res = await run_one(s, bus_publish=bus_publish, steps=cfg.steps)
                outcome.results.append(res)
            except Exception as exc:
                logger.exception("batch job {} failed", s.experiment_id)
                outcome.failures.append((s.experiment_id, str(exc)))

    await asyncio.gather(*(runner(s) for s in specs))
    return outcome
