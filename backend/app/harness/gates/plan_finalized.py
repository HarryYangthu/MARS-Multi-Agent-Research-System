"""Gate 1 — plan_finalized.

Triggered after Idea Agent approval, before Experiment Agent starts.
Implementation is a simple flag: once an idea_proposal.approved.md exists
the gate is open.
"""
from __future__ import annotations

from pathlib import Path

from app.harness.gates.gate_base import GateOutcome

GATE_ID = "plan_finalized"


def check(run_root: Path) -> GateOutcome:
    p = run_root / "idea" / "idea_proposal.approved.md"
    if p.exists():
        return GateOutcome(gate_id=GATE_ID, triggered=False, blocking=False, requires_human=False)
    return GateOutcome(
        gate_id=GATE_ID,
        triggered=True,
        blocking=True,
        requires_human=True,
        reason="idea_proposal.approved.md not found — Idea Agent must finish first",
    )
