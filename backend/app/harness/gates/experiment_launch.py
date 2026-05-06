"""Gate 3 — experiment_launch.

Estimates GPU hours from the experiment_plan and blocks if above threshold.
"""
from __future__ import annotations

from typing import Any

from app.harness.gates.gate_base import GateOutcome

GATE_ID = "experiment_launch"
DEFAULT_THRESHOLD_HOURS = 12


def check(metadata: dict[str, Any], *, threshold: float = DEFAULT_THRESHOLD_HOURS) -> GateOutcome:
    est = float(metadata.get("estimated_gpu_hours") or 0)
    if est > threshold:
        return GateOutcome(
            gate_id=GATE_ID,
            triggered=True,
            blocking=True,
            requires_human=True,
            reason=f"estimated GPU hours {est:.1f} > threshold {threshold:.1f}",
        )
    return GateOutcome(gate_id=GATE_ID, triggered=False, blocking=False, requires_human=False)
