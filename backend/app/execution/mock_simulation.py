"""Mock simulation (★ V0 critical when no GPU is available).

Per ACCEPTANCE §1.1 / DESIGN §16.2: produces a `run_log.v1` artifact whose
schema-shape is indistinguishable from a real run, plus a synthetic loss
curve and a few intermediate metric ticks. Hooks into the WS event bus so
the front-end split view sees realistic streaming behaviour.

Pure Python; ``numpy`` is the only heavy import and it's already a dep.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MockJob:
    run_id: str         # global run id (pipeline run)
    experiment_id: str  # ablation name
    project: str
    config: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 5.0   # tunable for tests; default short
    template: str = "exponential_decay"  # | "noisy_decay" | "plateau"
    seed: int | None = None


@dataclass
class MockResult:
    run_id: str
    experiment_id: str
    duration_seconds: float
    status: str  # "completed" | "failed" | "interrupted"
    metrics: dict[str, float]
    fingerprint_hash: str
    is_mock: bool = True


def _loss_curve(steps: int, *, template: str, seed: int | None) -> list[float]:
    rng = random.Random(seed)
    out: list[float] = []
    if template == "exponential_decay":
        for i in range(steps):
            v = math.exp(-i / max(1, steps // 4)) + rng.uniform(-0.01, 0.01)
            out.append(max(0.0, v))
    elif template == "noisy_decay":
        for i in range(steps):
            v = max(0.0, 1.0 / (1 + i * 0.05) + rng.uniform(-0.05, 0.05))
            out.append(v)
    else:  # plateau
        for i in range(steps):
            v = 0.4 + rng.uniform(-0.02, 0.02) if i > steps // 3 else 1.0 - i / max(1, steps // 3)
            out.append(max(0.0, v))
    return out


async def run_mock_simulation(
    job: MockJob,
    *,
    bus_publish: Any | None = None,    # async (channel, payload) -> None
    sleep_per_tick: float = 0.05,
    steps: int = 30,
) -> MockResult:
    """Run a single mock SimulationJob with WS streaming.

    Returns a MockResult; the caller serializes that into a run_log.v1 md.
    """
    started = time.monotonic()
    seed = (
        job.seed
        if job.seed is not None
        else int(hashlib.sha256(f"{job.run_id}:{job.experiment_id}".encode()).hexdigest()[:8], 16)
    )
    curve = _loss_curve(steps, template=job.template, seed=seed)

    channel = f"run.{job.run_id}.experiment.{job.experiment_id}"
    if bus_publish is not None:
        await bus_publish(
            channel,
            {"event": "execution.started", "experiment_id": job.experiment_id},
        )
    for i, v in enumerate(curve):
        if bus_publish is not None:
            await bus_publish(
                channel,
                {
                    "event": "execution.curve_point",
                    "experiment_id": job.experiment_id,
                    "step": i,
                    "metric": "loss",
                    "value": float(v),
                },
            )
        await asyncio.sleep(sleep_per_tick)

    elapsed = time.monotonic() - started
    final_loss = curve[-1] if curve else 0.0
    metrics = {
        "loss": final_loss,
        "RES": -42.0 + random.Random(seed).uniform(-1.5, 1.5),
        "PIM": -18.0 + random.Random(seed).uniform(-1.0, 1.0),
        "APE": 23.0 + random.Random(seed).uniform(-0.5, 0.5),
    }
    fingerprint_hash = "sha256:" + hashlib.sha256(
        f"{job.project}:{job.run_id}:{job.experiment_id}:{job.config}:{steps}:{job.template}".encode()
    ).hexdigest()[:24]

    if bus_publish is not None:
        await bus_publish(
            channel,
            {
                "event": "execution.completed",
                "experiment_id": job.experiment_id,
                "fingerprint_hash": fingerprint_hash,
            },
        )
    return MockResult(
        run_id=job.run_id,
        experiment_id=job.experiment_id,
        duration_seconds=elapsed,
        status="completed",
        metrics=metrics,
        fingerprint_hash=fingerprint_hash,
    )
