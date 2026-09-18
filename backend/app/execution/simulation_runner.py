"""Explicit real PIM CPU, paper-static or configured local-command execution."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import shutil
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.execution.results import SimulationResult
from app.settings import get_settings
from app.harness.persistence import atomic_write_json
from app.harness.tools.config import load_execution_config
from app.harness.tools.process_runtime import start_process, terminate_process_tree


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


async def _run_real_pim_in_worker(
    spec: JobSpec,
    *,
    bus_publish: Any | None = None,
    sleep_per_tick: float = 0.05,
    steps: int = 60,
) -> SimulationResult:
    """Run the real dual-carrier PIM cancellation for one ablation."""
    from app.execution.pim_cancellation import (
        DEFAULT_N_POINTS,
        plot_loss_curve,
        run_pim_cancellation,
    )

    started = time.monotonic()
    seed = _seed_for(spec)
    channel = f"run.{spec.run_id}.experiment.{spec.experiment_id}"
    n_steps = max(steps, 1)
    n_points = int(spec.config.get("n_points", DEFAULT_N_POINTS))
    if n_points < 1024 or n_points > DEFAULT_N_POINTS:
        raise ValueError(f"pim_cpu n_points must be in [1024, {DEFAULT_N_POINTS}]")
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
                "n_points": n_points,
            },
        )

    loop = asyncio.get_running_loop()
    step_queue: asyncio.Queue[tuple[int, float, list[float]]] = asyncio.Queue()

    def _on_step(step: int, value: float, curve: list[float]) -> None:
        loop.call_soon_threadsafe(step_queue.put_nowait, (step, value, curve))

    def _run() -> Any:
        return run_pim_cancellation(
            n_points=n_points,
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
        return SimulationResult(
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
    return SimulationResult(
        run_id=spec.run_id,
        experiment_id=spec.experiment_id,
        duration_seconds=elapsed,
        status="completed",
        metrics=metrics,
        fingerprint_hash=fingerprint_hash,
        is_mock=False,
        loss_curve=[float(v) for v in res.loss_curve],
    )


async def run_real_pim_simulation(
    spec: JobSpec, *, bus_publish: Any | None = None, sleep_per_tick: float = 0.05, steps: int = 60,
) -> SimulationResult:
    """Run numerical work in a killable CPU worker, with a host-owned receipt."""
    if spec.run_root is None:
        raise RuntimeError("pim_cpu requires a persistent run_root")
    started = time.monotonic()
    attempt_dir = spec.run_root / "execution/pim_cpu" / _safe_name(spec.experiment_id) / uuid.uuid4().hex
    attempt_dir.mkdir(parents=True, exist_ok=False)
    request_path, result_path = attempt_dir / "job.json", attempt_dir / "result.json"
    payload = {"spec": {**asdict(spec), "run_root": str(spec.run_root.resolve())},
               "steps": steps, "sleep_per_tick": sleep_per_tick, "result_path": str(result_path.resolve())}
    atomic_write_json(request_path, payload)
    request_hash = "sha256:" + hashlib.sha256(request_path.read_bytes()).hexdigest()
    cfg = load_execution_config()["execution"]
    timeout = float(cfg.get("pim_cpu_timeout_seconds", cfg.get("command_timeout_seconds", 60)))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("pim_cpu timeout must be positive and finite")
    argv = [sys.executable, "-m", "app.execution.pim_cpu_worker", str(request_path.resolve())]
    channel = f"run.{spec.run_id}.experiment.{spec.experiment_id}"
    result = SimulationResult(run_id=spec.run_id, experiment_id=spec.experiment_id,
                              duration_seconds=0, status="failed", metrics={}, fingerprint_hash="", is_mock=False)
    process: asyncio.subprocess.Process | None = None
    error = ""
    interrupted: BaseException | None = None
    with (attempt_dir / "stderr.log").open("w", encoding="utf-8") as stderr:
        try:
            process = await start_process(argv, cwd=attempt_dir, stdin=asyncio.subprocess.DEVNULL, stderr=stderr,
                env={"PYTHONPATH": str(Path(__file__).resolve().parents[2]), "PYTHONSAFEPATH": "1",
                     "CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
            atomic_write_json(attempt_dir / "process.json", {"pid": process.pid, "argv": argv})

            async def forward_events() -> None:
                assert process is not None and process.stdout is not None
                async for raw in process.stdout:
                    message = json.loads(raw)
                    event = message.get("payload", {})
                    if not isinstance(event, dict):
                        raise ValueError("pim_cpu worker emitted an invalid event")
                    if bus_publish is not None and event.get("event") not in {"execution.completed", "execution.failed"}:
                        await bus_publish(channel, event)

            await asyncio.wait_for(asyncio.gather(forward_events(), process.wait()), timeout=timeout)
            if process.returncode != 0 or not result_path.is_file() or result_path.is_symlink():
                raise ValueError("pim_cpu worker exited without successful result artifact")
            if "sha256:" + hashlib.sha256(request_path.read_bytes()).hexdigest() != request_hash:
                raise ValueError("pim_cpu worker altered its host request")
            raw_result = json.loads(result_path.read_text(encoding="utf-8"))
            measured = SimulationResult(**raw_result)
            if (measured.is_mock is not False or measured.status != "completed" or measured.run_id != spec.run_id
                    or measured.experiment_id != spec.experiment_id or not measured.metrics
                    or any(isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value)
                           for value in [*measured.metrics.values(), *measured.loss_curve])):
                raise ValueError("pim_cpu worker result failed measurement validation")
            result = measured
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        except BaseException as exc:
            result.status, error, interrupted = "cancelled", type(exc).__name__, exc
        finally:
            if process is not None:
                await terminate_process_tree(process)
    result.duration_seconds = time.monotonic() - started
    receipt_path = attempt_dir / "execution_receipt.json"
    atomic_write_json(receipt_path, {"schema": "pim_cpu_receipt.v1", "run_id": spec.run_id,
        "experiment_id": spec.experiment_id, "status": result.status,
        "returncode": process.returncode if process is not None else None,
        "request_sha256": request_hash,
        "result_sha256": "sha256:" + hashlib.sha256(result_path.read_bytes()).hexdigest() if result_path.is_file() else None,
        "execution_backend": "local_process", "os_isolated": False, "argv": argv, "error": error})
    result.fingerprint_hash = "sha256:" + hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    if interrupted is not None:
        raise interrupted
    if bus_publish is not None:
        await bus_publish(channel, {"event": "execution.completed" if result.status == "completed" else "execution.failed",
            "experiment_id": spec.experiment_id, "metrics": result.metrics, "error": error,
            "fingerprint_hash": result.fingerprint_hash, "receipt_path": str(receipt_path)})
    return result


async def run_one(spec: JobSpec, *, bus_publish: Any | None = None, steps: int = 30) -> SimulationResult:
    settings = get_settings()
    backend = settings.mars_execution_backend
    if backend == "local_command":
        from app.harness.tools.execution.local_command import LocalCommandJob, run_local_command

        if spec.run_root is None:
            raise RuntimeError("no configured execution adapter: local_command requires a persistent run_root")
        channel = f"run.{spec.run_id}.experiment.{spec.experiment_id}"
        if bus_publish is not None:
            await bus_publish(channel, {"event": "execution.started", "experiment_id": spec.experiment_id,
                                        "kind": "local_command"})
        outcome = await run_local_command(LocalCommandJob(
            run_id=spec.run_id, experiment_id=spec.experiment_id, project=spec.project,
            run_root=spec.run_root, config=dict(spec.config), seed=spec.seed, steps=steps,
            command_id=str(spec.config.get("command_id") or ""),
        ))
        if bus_publish is not None:
            await bus_publish(channel, {"event": "execution.completed" if outcome.status == "completed" else "execution.failed",
                                        "experiment_id": spec.experiment_id, "metrics": outcome.metrics,
                                        "fingerprint_hash": outcome.fingerprint_hash, "artifacts": outcome.artifacts,
                                        "error": outcome.error})
        return SimulationResult(run_id=spec.run_id, experiment_id=spec.experiment_id,
            duration_seconds=outcome.duration_seconds, status=outcome.status, metrics=outcome.metrics,
            fingerprint_hash=outcome.fingerprint_hash, is_mock=False, loss_curve=outcome.loss_curve)
    use_real = spec.project == "pimc" and spec.run_root is not None
    if use_real and backend == "paper_static":
        from app.execution.paper_static_adapter import run_paper_static_simulation

        return await run_paper_static_simulation(spec, bus_publish=bus_publish, steps=steps)
    if use_real and backend == "pim_cpu":
        return await run_real_pim_simulation(spec, bus_publish=bus_publish, steps=steps)

    raise RuntimeError(f"no configured execution adapter for backend {backend!r} and project {spec.project!r}")


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value) or "experiment"
