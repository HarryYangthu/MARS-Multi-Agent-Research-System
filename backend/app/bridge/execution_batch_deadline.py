"""Bind the outer dispatch deadline to the confirmed per-job execution limits."""
from __future__ import annotations

import math
from typing import Any

from app.bridge.execution_confirmation import require_confirmation
from app.harness.tools.registry import ToolRegistry, get_registry
from app.storage.run_store import RunHandle


def batch_deadline_seconds(view: dict[str, Any], overhead_seconds: float) -> float:
    """Sequential upper bound remains safe for any approved concurrency.

    Queuing time belongs to the batch, never to an individual job deadline.
    Keep the configured dispatch allowance for setup, evidence and cleanup.
    """
    def positive(value: Any) -> float:
        if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
            raise ValueError("execution timeout must be positive and finite")
        return float(value)

    if view.get("blockers") or not view.get("confirmed"):
        raise ValueError("execution configuration must be confirmed before computing its deadline")
    experiments = view.get("experiments")
    if not isinstance(experiments, list) or not experiments:
        raise ValueError("confirmed execution has no jobs")
    defaults = view["defaults"]
    limits = [positive(item.get("effective", {}).get("timeout_seconds", defaults["timeout_seconds"]))
              for item in experiments]
    deadline = math.fsum(limits) + positive(overhead_seconds)
    return positive(deadline)


def execution_batch_registry(run: RunHandle, node_key: str) -> ToolRegistry:
    registry = get_registry()
    spec = registry.spec("execution.batch_runner")
    if spec is None:
        raise ValueError("execution batch tool is unavailable")
    view = require_confirmation(run, node_key)
    return registry.with_timeout(spec.name, batch_deadline_seconds(view, spec.policy.timeout_seconds))


def prepare_interrupted_execution_retry(run: RunHandle, node_key: str) -> None:
    from app.bridge.execution_batch_plan import prepare_execution
    from app.execution.job_journal import rearm_interrupted_jobs
    view = require_confirmation(run, node_key)
    prepared = prepare_execution(run, node_key)
    for spec in prepared.specs:
        spec.config['confirmation_token'] = view['token']
    rearm_interrupted_jobs(prepared.specs, steps=prepared.batch_steps)
