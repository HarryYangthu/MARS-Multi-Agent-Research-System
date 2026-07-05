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
    loss_curve: list[float] = field(default_factory=list)


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


def _config_memory(config: dict[str, Any]) -> int:
    """Resolve the canceller memory depth from an ablation config."""
    for key in ("expert_count", "experts", "n_experts", "memory"):
        if key in config:
            try:
                return max(1, min(48, int(config[key])))
            except (TypeError, ValueError):
                continue
    return 4


def _res_for_memory(memory: int, *, seed: int) -> float:
    """Monotone fit to the real canceller (pim_cancellation.py).

    Shallow memory leaves uncancelled PIM (high residual); deep memory (>= the
    true 12 taps) saturates near the -29 dB noise floor. Keeps the mock backend
    physically consistent with the real one so the demo's fail->fix arc holds in
    either backend.
    """
    base = -29.0 + 14.0 * math.exp(-(memory - 2) / 3.5)
    jitter = random.Random(seed ^ 0x5151).uniform(-0.4, 0.4)
    return min(-12.0, base + jitter)


def _ape_for_res(res_db: float, *, seed: int) -> float:
    """Worse cancellation (higher residual) -> larger residual phase error."""
    base = 23.0 + max(0.0, res_db + 29.0) * 1.15
    return base + random.Random(seed ^ 0x2727).uniform(-0.5, 0.5)


def _capacity_loss_curve(
    steps: int, *, final_loss: float, template: str, seed: int
) -> list[float]:
    """Decay curve that lands on ``final_loss`` so loss == 10^(RES/10) holds."""
    rng = random.Random(seed)
    floor = max(1e-4, final_loss)
    tau = max(1.0, steps / 4.0)
    out: list[float] = []
    for i in range(steps):
        v = floor + (1.0 - floor) * math.exp(-i / tau)
        if template == "noisy_decay":
            v *= 1.0 + rng.uniform(-0.06, 0.06)
        elif template == "plateau" and i > steps // 3:
            v = max(v, floor * 3.0)
        else:
            v += rng.uniform(-0.004, 0.004)
        out.append(max(floor * 0.9, v))
    if out:
        out[-1] = floor
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
    memory = _config_memory(job.config)
    res_db = _res_for_memory(memory, seed=seed)
    final_loss = 10.0 ** (res_db / 10.0)
    curve = _capacity_loss_curve(
        steps, final_loss=final_loss, template=job.template, seed=seed
    )

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
    metrics = {
        "loss": round(float(final_loss), 6),
        "RES": round(res_db, 3),
        "PIM": round(-res_db, 3),
        "APE": round(_ape_for_res(res_db, seed=seed), 3),
        "n_basis": float(4 * memory),
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
        loss_curve=[float(v) for v in curve],
    )
