"""Gate 4 — conclusion_output.

Triggered after Writing Agent finishes; the report must be human-approved
before it lands in the methodology KB.
"""
from __future__ import annotations

from pathlib import Path

from app.harness.gates.gate_base import GateOutcome

GATE_ID = "conclusion_output"


def check(run_root: Path) -> GateOutcome:
    p = run_root / "writing" / "research_report.approved.md"
    if p.exists():
        return GateOutcome(gate_id=GATE_ID, triggered=False, blocking=False, requires_human=False)
    return GateOutcome(
        gate_id=GATE_ID,
        triggered=True,
        blocking=True,
        requires_human=True,
        reason="research_report.approved.md missing — needs human sign-off",
    )
