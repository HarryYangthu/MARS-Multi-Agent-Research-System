"""Single-experiment runner.

For the pimc project this runs a REAL (lightweight) dual-carrier PIM
cancellation simulation on CPU (see ``pim_cancellation.py``) — generating a
real ~30k-point complex dual-carrier signal, fitting a memory-polynomial
canceller, and emitting a real loss curve + RES/PIM/APE metrics.

Falls back to the synthetic mock simulation when ``MARS_MOCK_MODE=always`` or
for non-PIM projects. GPU training of the full 7-layer model is V2.
"""
from __future__ import annotations

import asyncio
import hashlib
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from app.execution.config import get_execution_config
from app.execution.mock_simulation import MockJob, MockResult, run_mock_simulation
from app.settings import get_settings


def gpu_available() -> bool:
    """Cheap GPU probe — does ``nvidia-smi`` exist on PATH?"""
    return shutil.which("nvidia-smi") is not None


@dataclass
class JobSpec:
    run_id: str
    experiment_id: str
    project: str
    config: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 5.0
    seed: int | None = None
    template: str = "exponential_decay"
    run_root: Path | None = None
    plot_every_steps: int = 5


def _seed_for(spec: JobSpec) -> int:
    if spec.seed is not None:
        return spec.seed
    return int(
        hashlib.sha256(f"{spec.run_id}:{spec.experiment_id}".encode()).hexdigest()[:8],
        16,
    )


async def run_real_pim_simulation(
    spec: JobSpec,
    *,
    bus_publish: Any | None = None,
    sleep_per_tick: float = 0.05,
    steps: int = 60,
) -> MockResult:
    """Run the real dual-carrier PIM cancellation for one ablation."""
    from app.execution.pim_cancellation import (
        DEFAULT_N_POINTS,
        plot_loss_curve,
        run_pim_cancellation,
    )

    started = time.monotonic()
    seed = _seed_for(spec)
    channel = f"run.{spec.run_id}.experiment.{spec.experiment_id}"
    n_steps = max(steps, 60)
    plot_dir = spec.run_root / "execution" / "live_plots" if spec.run_root is not None else None
    plot_filename = f"{_safe_name(spec.experiment_id)}_loss.png"
    plot_path = plot_dir / plot_filename if plot_dir is not None else None
    plot_every = max(1, spec.plot_every_steps)

    if bus_publish is not None:
        await bus_publish(
            channel,
            {
                "event": "execution.started",
                "experiment_id": spec.experiment_id,
                "kind": "real_pim",
                "n_points": DEFAULT_N_POINTS,
            },
        )

    loop = asyncio.get_running_loop()
    step_queue: asyncio.Queue[tuple[int, float, list[float]]] = asyncio.Queue()

    def _on_step(step: int, value: float, curve: list[float]) -> None:
        loop.call_soon_threadsafe(step_queue.put_nowait, (step, value, curve))

    def _run() -> Any:
        return run_pim_cancellation(
            n_points=DEFAULT_N_POINTS,
            steps=n_steps,
            ablation_config=dict(spec.config),
            seed=seed,
            on_step=_on_step,
            step_delay_seconds=sleep_per_tick,
        )

    async def _write_live_plot(step: int, curve: list[float]) -> None:
        if plot_path is None:
            return
        plot_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(
                plot_loss_curve,
                curve,
                plot_path,
                total_steps=n_steps,
                experiment_id=spec.experiment_id,
                title="Live PIM Cancellation Loss",
            )
        except Exception as exc:
            if bus_publish is not None:
                await bus_publish(
                    channel,
                    {
                        "event": "execution.plot_failed",
                        "experiment_id": spec.experiment_id,
                        "metric": "loss",
                        "step": step,
                        "error": str(exc),
                    },
                )
            return
        if bus_publish is not None:
            await bus_publish(
                channel,
                {
                    "event": "execution.plot_updated",
                    "experiment_id": spec.experiment_id,
                    "metric": "loss",
                    "step": step,
                    "total_steps": n_steps,
                    "filename": plot_filename,
                    "plot_url": f"/api/execution/{spec.run_id}/plots/{plot_filename}",
                    "cache_bust": time.time_ns(),
                },
            )

    task = asyncio.create_task(asyncio.to_thread(_run))
    last_plotted_step = -1
    last_curve: list[float] = []
    try:
        while True:
            if task.done() and step_queue.empty():
                break
            try:
                step, value, curve = await asyncio.wait_for(
                    step_queue.get(),
                    timeout=0.25,
                )
            except asyncio.TimeoutError:
                continue
            last_curve = curve
            if bus_publish is not None:
                await bus_publish(
                    channel,
                    {
                        "event": "execution.curve_point",
                        "experiment_id": spec.experiment_id,
                        "step": step,
                        "metric": "loss",
                        "value": float(value),
                    },
                )
            if step == 0 or (step + 1) % plot_every == 0 or step == n_steps - 1:
                await _write_live_plot(step, curve)
                last_plotted_step = step
        _data, res = await task
    except Exception as exc:
        if bus_publish is not None:
            await bus_publish(
                channel,
                {"event": "execution.failed", "experiment_id": spec.experiment_id, "error": str(exc)},
            )
        return MockResult(
            run_id=spec.run_id,
            experiment_id=spec.experiment_id,
            duration_seconds=time.monotonic() - started,
            status="failed",
            metrics={},
            fingerprint_hash="",
            is_mock=False,
        )
    if res.loss_curve and last_plotted_step != len(res.loss_curve) - 1:
        await _write_live_plot(len(res.loss_curve) - 1, last_curve or res.loss_curve)

    elapsed = time.monotonic() - started
    metrics = {
        "loss": float(res.final_loss),
        "RES": float(res.res_db),
        "PIM": float(res.pim_suppression_db),
        "APE": float(res.ape_deg),
        "n_basis": float(res.n_basis),
    }
    fingerprint_hash = "sha256:" + hashlib.sha256(
        f"{spec.project}:{spec.run_id}:{spec.experiment_id}:{spec.config}:pim".encode()
    ).hexdigest()[:24]

    if bus_publish is not None:
        await bus_publish(
            channel,
            {
                "event": "execution.completed",
                "experiment_id": spec.experiment_id,
                "fingerprint_hash": fingerprint_hash,
                "metrics": metrics,
            },
        )
    return MockResult(
        run_id=spec.run_id,
        experiment_id=spec.experiment_id,
        duration_seconds=elapsed,
        status="completed",
        metrics=metrics,
        fingerprint_hash=fingerprint_hash,
        is_mock=False,
        loss_curve=[float(v) for v in res.loss_curve],
    )


async def run_one(spec: JobSpec, *, bus_publish: Any | None = None, steps: int = 30) -> MockResult:
    settings = get_settings()
    if settings.is_production and settings.mars_execution_backend == "mock":
        raise RuntimeError("production mode cannot use mock execution backend")
    backend = settings.mars_execution_backend
    use_real = settings.mars_mock_mode != "always" and spec.project == "pimc"
    if use_real and backend == "paper_static":
        from app.execution.paper_static_adapter import run_paper_static_simulation

        return await run_paper_static_simulation(spec, bus_publish=bus_publish, steps=steps)
    if use_real and backend == "pim_cpu":
        return await run_real_pim_simulation(spec, bus_publish=bus_publish, steps=steps)

    job = MockJob(
        run_id=spec.run_id,
        experiment_id=spec.experiment_id,
        project=spec.project,
        config=spec.config,
        duration_seconds=spec.duration_seconds,
        template=spec.template,
        seed=spec.seed,
    )
    return await run_mock_simulation(job, bus_publish=bus_publish, steps=steps)


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value) or "experiment"
