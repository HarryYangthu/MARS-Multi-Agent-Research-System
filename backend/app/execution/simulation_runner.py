"""Single-experiment runner.

V0 always uses mock_simulation by default. When GPU is detected and
``MARS_MOCK_MODE != always``, callers may pass a custom subprocess command
(left for V1 once the real research code is wired in).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import Any

from app.execution.mock_simulation import MockJob, MockResult, run_mock_simulation


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


async def run_one(spec: JobSpec, *, bus_publish: Any | None = None, steps: int = 30) -> MockResult:
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
