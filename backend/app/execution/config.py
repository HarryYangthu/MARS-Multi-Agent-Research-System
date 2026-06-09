"""Loads ``configs/execution.yaml`` — the single source of truth for
simulation concurrency and step counts.

Falls back to built-in defaults if the file is missing so the system still
runs in a bare checkout. Cached for the process lifetime.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml
from loguru import logger

from app.settings import repo_root


@dataclass(frozen=True)
class ExecutionConfig:
    max_concurrency: int
    default_steps: int
    agent_batch_steps: int
    backend: str  # "mock" | "real"
    job_timeout_seconds: float
    feedback_max_attempts: int
    planned_experiments: int
    tick_seconds: float


_DEFAULTS = ExecutionConfig(
    max_concurrency=6,
    default_steps=30,
    agent_batch_steps=20,
    backend="mock",
    job_timeout_seconds=120.0,
    feedback_max_attempts=2,
    planned_experiments=16,
    tick_seconds=0.05,
)


@lru_cache(maxsize=1)
def get_execution_config() -> ExecutionConfig:
    path = repo_root() / "configs" / "execution.yaml"
    if not path.exists():
        logger.warning(
            "configs/execution.yaml missing — using execution defaults {}", _DEFAULTS
        )
        return _DEFAULTS
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    conc = data.get("concurrency", {}) or {}
    sim = data.get("simulation", {}) or {}
    fb = data.get("feedback_loop", {}) or {}
    backend = str(data.get("backend", _DEFAULTS.backend)).lower()
    if backend not in ("mock", "real"):
        backend = _DEFAULTS.backend
    return ExecutionConfig(
        max_concurrency=int(conc.get("max_concurrent", _DEFAULTS.max_concurrency)),
        default_steps=int(sim.get("default_steps", _DEFAULTS.default_steps)),
        agent_batch_steps=int(sim.get("agent_batch_steps", _DEFAULTS.agent_batch_steps)),
        backend=backend,
        job_timeout_seconds=float(
            data.get("job_timeout_seconds", _DEFAULTS.job_timeout_seconds)
        ),
        feedback_max_attempts=int(fb.get("max_attempts", _DEFAULTS.feedback_max_attempts)),
        planned_experiments=int(sim.get("planned_experiments", _DEFAULTS.planned_experiments)),
        tick_seconds=float(sim.get("tick_seconds", _DEFAULTS.tick_seconds)),
    )
