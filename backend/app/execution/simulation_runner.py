"""Single-experiment runner.

Two backends, chosen by ``configs/execution.yaml::backend``:

* ``mock`` — synthetic loss curve, zero external deps (V0 default, CLAUDE.md #9).
* ``real`` — run the project repo's ``main.py`` as a CPU subprocess and parse
  its ``metrics.json`` / ``loss.json`` output. Falls back to mock if the repo
  or its ``main.py`` is missing, so the e2e demo never breaks.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from app.execution.config import get_execution_config
from app.execution.mock_simulation import MockJob, MockResult, run_mock_simulation
from app.settings import repo_root


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


def _resolve_repo_main(project: str) -> Path | None:
    """Locate the project repo's main.py (real backend entrypoint).

    Honours ``projects/<project>/repo_link.yaml::repo_path``; falls back to the
    in-repo synthetic trainer ``workspace/repos/pimc-stub/main.py``.
    """
    root = repo_root()
    proj_dir = root / "projects" / project
    link = proj_dir / "repo_link.yaml"
    if link.exists():
        try:
            data = yaml.safe_load(link.read_text(encoding="utf-8")) or {}
            repo_path = str(data.get("repo_path", "") or "")
            if repo_path:
                candidate = (proj_dir / repo_path).resolve() / "main.py"
                if candidate.exists():
                    return candidate
        except (OSError, yaml.YAMLError):
            pass
    fallback = root / "workspace" / "repos" / "pimc-stub" / "main.py"
    return fallback if fallback.exists() else None


def _real_seed(spec: JobSpec) -> int:
    if spec.seed is not None:
        return spec.seed
    return int(
        hashlib.sha256(f"{spec.run_id}:{spec.experiment_id}".encode()).hexdigest()[:8],
        16,
    )


async def _run_real_experiment(
    spec: JobSpec, *, bus_publish: Any | None, steps: int, timeout: float
) -> MockResult:
    """Run the project's main.py as a subprocess and parse its output."""
    main_py = _resolve_repo_main(spec.project)
    if main_py is None:
        raise FileNotFoundError(
            f"real backend: no main.py for project '{spec.project}'"
        )

    channel = f"run.{spec.run_id}.experiment.{spec.experiment_id}"
    seed = _real_seed(spec)
    out_dir = Path(tempfile.mkdtemp(prefix=f"mars_{spec.experiment_id}_"))

    cmd = [
        sys.executable,
        str(main_py),
        "--experiment-id", spec.experiment_id,
        "--config", json.dumps(spec.config or {}),
        "--seed", str(seed),
        "--output-dir", str(out_dir),
        "--steps", str(steps),
    ]
    if bus_publish is not None:
        await bus_publish(
            channel, {"event": "execution.started", "experiment_id": spec.experiment_id}
        )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(main_py.parent),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _pump_stdout() -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line.startswith("@curve ") and bus_publish is not None:
                parts = line.split()
                if len(parts) == 3:  # @curve <step> <loss>
                    await bus_publish(
                        channel,
                        {
                            "event": "execution.curve_point",
                            "experiment_id": spec.experiment_id,
                            "step": int(parts[1]),
                            "metric": "loss",
                            "value": float(parts[2]),
                        },
                    )
            elif bus_publish is not None:
                await bus_publish(
                    channel, {"event": "execution.log_line", "line": line}
                )

    try:
        await asyncio.wait_for(
            asyncio.gather(_pump_stdout(), proc.wait()), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(
            f"experiment {spec.experiment_id} exceeded {timeout}s and was killed"
        ) from None

    if proc.returncode != 0:
        err = b""
        if proc.stderr is not None:
            err = await proc.stderr.read()
        raise RuntimeError(
            f"experiment {spec.experiment_id} failed (exit {proc.returncode}): "
            f"{err.decode(errors='replace')[-500:]}"
        )

    metrics_path = out_dir / "metrics.json"
    if not metrics_path.exists():
        raise RuntimeError(f"experiment {spec.experiment_id} produced no metrics.json")
    metrics = {k: float(v) for k, v in json.loads(metrics_path.read_text()).items()}

    fingerprint_hash = "sha256:" + hashlib.sha256(
        f"{spec.project}:{spec.run_id}:{spec.experiment_id}:{spec.config}:real".encode()
    ).hexdigest()[:24]
    if bus_publish is not None:
        await bus_publish(
            channel,
            {
                "event": "execution.completed",
                "experiment_id": spec.experiment_id,
                "fingerprint_hash": fingerprint_hash,
            },
        )
    return MockResult(
        run_id=spec.run_id,
        experiment_id=spec.experiment_id,
        duration_seconds=0.0,
        status="completed",
        metrics=metrics,
        fingerprint_hash=fingerprint_hash,
        is_mock=False,
    )


async def run_one(
    spec: JobSpec,
    *,
    bus_publish: Any | None = None,
    steps: int = 30,
    tick_seconds: float = 0.05,
    backend: str | None = None,
) -> MockResult:
    cfg = get_execution_config()
    chosen = (backend or cfg.backend).lower()

    if chosen == "real":
        if _resolve_repo_main(spec.project) is not None:
            return await _run_real_experiment(
                spec,
                bus_publish=bus_publish,
                steps=steps,
                timeout=cfg.job_timeout_seconds,
            )
        logger.warning(
            "real backend requested but no main.py for '{}' — falling back to mock",
            spec.project,
        )

    job = MockJob(
        run_id=spec.run_id,
        experiment_id=spec.experiment_id,
        project=spec.project,
        config=spec.config,
        duration_seconds=spec.duration_seconds,
        template=spec.template,
        seed=spec.seed,
    )
    return await run_mock_simulation(
        job, bus_publish=bus_publish, sleep_per_tick=tick_seconds, steps=steps
    )
