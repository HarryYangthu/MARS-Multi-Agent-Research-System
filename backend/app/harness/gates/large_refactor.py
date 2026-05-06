"""Gate 2 — large_refactor.

Triggered when the Coding Agent's code_spec lists ≥ N changed files.
"""
from __future__ import annotations

from typing import Any

from app.harness.gates.gate_base import GateOutcome

GATE_ID = "large_refactor"
DEFAULT_THRESHOLD = 5


def check(metadata: dict[str, Any], *, threshold: int = DEFAULT_THRESHOLD) -> GateOutcome:
    files = metadata.get("files_changed", []) or []
    n = len(files) if isinstance(files, list) else 0
    if n >= threshold:
        return GateOutcome(
            gate_id=GATE_ID,
            triggered=True,
            blocking=True,
            requires_human=True,
            reason=f"{n} files changed (threshold={threshold})",
        )
    return GateOutcome(gate_id=GATE_ID, triggered=False, blocking=False, requires_human=False)
